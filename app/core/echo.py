"""Echo-suppression pipeline.

Every inbound change — from a Notion webhook, a Notion poll, or a Google poll —
is funneled through :func:`should_propagate` before anything is written to the
other side. This is what stops a write to one system from bouncing back as a
phantom edit and looping forever.

The decision steps (per facet):

1. **verify** — the event must carry a content hash.
2. **actor filter** — Notion edits attributed to our own bot are dropped.
3. **fetch & hash** — done by the caller; provided as ``incoming_hash``.
4. **unchanged?** — equal to the last-known hash for this side ⇒ drop.
5. **inflight?** — matches a marker we set when we wrote it ⇒ drop (echo).
6. **propagate** — otherwise the change is real; the engine performs the write.

After a successful write the engine calls :func:`mark_propagated` to set an
inflight marker on the destination and record the new hash, which closes the loop
in a single hop (step 5 catches the bounce-back).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sqlmodel import Session

from app.config import Settings
from app.ledger import repo
from app.ledger.models import SyncPair


class Decision(StrEnum):
    DROP_NO_HASH = "drop_no_hash"
    DROP_OWN_ACTOR = "drop_own_actor"
    DROP_UNCHANGED = "drop_unchanged"
    DROP_ECHO = "drop_echo"
    PROPAGATE = "propagate"


@dataclass
class SyncEvent:
    system: str  # 'notion' | 'google' — where the change originated
    facet: str  # 'property' | 'body'
    incoming_hash: str | None
    actor_id: str | None = None
    edited_at: str | None = None


@dataclass
class EchoResult:
    decision: Decision
    reason: str = ""

    @property
    def propagate(self) -> bool:
        return self.decision is Decision.PROPAGATE


def _stored_hash(pair: SyncPair, system: str, facet: str) -> str | None:
    attr = {
        ("notion", "property"): "notion_prop_hash",
        ("notion", "body"): "notion_body_hash",
        ("google", "property"): "g_prop_hash",
        ("google", "body"): "g_body_hash",
    }[(system, facet)]
    return getattr(pair, attr)


def _set_hash(pair: SyncPair, system: str, facet: str, value: str) -> None:
    attr = {
        ("notion", "property"): "notion_prop_hash",
        ("notion", "body"): "notion_body_hash",
        ("google", "property"): "g_prop_hash",
        ("google", "body"): "g_body_hash",
    }[(system, facet)]
    setattr(pair, attr, value)


def should_propagate(
    session: Session,
    pair: SyncPair | None,
    event: SyncEvent,
    settings: Settings,
) -> EchoResult:
    """Run steps 1, 2, 4, 5. Returns whether the engine should propagate."""
    # Step 1: verify.
    if not event.incoming_hash:
        return EchoResult(Decision.DROP_NO_HASH, "event carried no hash")

    # Step 2: actor filter (Notion edits by our own bot are our writes).
    if (
        event.system == "notion"
        and event.actor_id
        and settings.sync_bot_notion_user_id
        and event.actor_id == settings.sync_bot_notion_user_id
    ):
        return EchoResult(Decision.DROP_OWN_ACTOR, "edited by sync bot")

    # A brand-new pair (no ledger row yet) is always a real change.
    if pair is None:
        return EchoResult(Decision.PROPAGATE, "new item")

    # Step 4: unchanged relative to last-known hash for this side.
    if _stored_hash(pair, event.system, event.facet) == event.incoming_hash:
        return EchoResult(Decision.DROP_UNCHANGED, "hash matches last-known")

    # Step 5: inflight — is this the bounce-back of our own write?
    if repo.consume_inflight(
        session, pair.pair_id, event.system, event.facet, event.incoming_hash
    ):
        # Now that we have observed the destination's actual content, record its
        # real hash so future unchanged events are caught cheaply at step 4.
        _set_hash(pair, event.system, event.facet, event.incoming_hash)
        session.add(pair)
        session.commit()
        return EchoResult(Decision.DROP_ECHO, "matched inflight marker")

    return EchoResult(Decision.PROPAGATE, "real change")


def record_source(
    session: Session, pair: SyncPair, event: SyncEvent
) -> None:
    """Persist the source side's new hash after a propagated change."""
    _set_hash(pair, event.system, event.facet, event.incoming_hash)
    if event.edited_at:
        if event.system == "notion":
            pair.notion_edited = event.edited_at
        else:
            pair.g_edited = event.edited_at
    session.add(pair)
    session.commit()


def mark_propagated(
    session: Session,
    pair: SyncPair,
    dest_system: str,
    facet: str,
    new_hash: str,
    settings: Settings,
) -> None:
    """After writing to the destination, set an inflight marker + store its hash.

    The inflight marker means: "the next inbound ``dest_system``/``facet`` event
    with this hash is our own echo — drop it (step 5)." We deliberately do *not*
    overwrite the destination's stored hash here: ``new_hash`` is only our
    *prediction* of what the destination will contain. The real hash is recorded
    when the echo is observed and consumed (see :func:`should_propagate`).
    """
    repo.set_inflight(
        session,
        pair.pair_id,
        dest_system,
        facet,
        new_hash,
        settings.inflight_ttl_seconds,
    )
