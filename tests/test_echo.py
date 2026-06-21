"""Echo-suppression pipeline behavior."""

from app.core.echo import (
    Decision,
    SyncEvent,
    mark_propagated,
    record_source,
    should_propagate,
)
from app.ledger import repo


def _pair(session, **kw):
    return repo.upsert_pair(session, notion_id="n1", kind="action", **kw)


def test_new_item_propagates(session, settings):
    ev = SyncEvent(system="notion", facet="property", incoming_hash="h1")
    res = should_propagate(session, None, ev, settings)
    assert res.decision is Decision.PROPAGATE


def test_no_hash_dropped(session, settings):
    ev = SyncEvent(system="notion", facet="property", incoming_hash=None)
    res = should_propagate(session, None, ev, settings)
    assert res.decision is Decision.DROP_NO_HASH


def test_own_actor_dropped(session, settings):
    pair = _pair(session)
    ev = SyncEvent(
        system="notion",
        facet="property",
        incoming_hash="h1",
        actor_id="bot-user-123",
    )
    res = should_propagate(session, pair, ev, settings)
    assert res.decision is Decision.DROP_OWN_ACTOR


def test_unchanged_dropped(session, settings):
    pair = _pair(session, notion_prop_hash="h1")
    ev = SyncEvent(system="notion", facet="property", incoming_hash="h1")
    res = should_propagate(session, pair, ev, settings)
    assert res.decision is Decision.DROP_UNCHANGED


def test_propagate_then_echo_is_suppressed(session, settings):
    """Full loop: a Notion change propagates to Google; the Google poll then sees
    that same content and must be dropped as an echo."""
    pair = _pair(session)
    # Notion change comes in.
    ev_in = SyncEvent(system="notion", facet="property", incoming_hash="hN")
    assert should_propagate(session, pair, ev_in, settings).propagate
    record_source(session, pair, ev_in)
    # We write to Google with canonical hash hG and mark it inflight.
    mark_propagated(session, pair, "google", "property", "hG", settings)

    # The Google poll later reports the row we just wrote.
    ev_echo = SyncEvent(system="google", facet="property", incoming_hash="hG")
    res = should_propagate(session, pair, ev_echo, settings)
    assert res.decision is Decision.DROP_ECHO

    # And the marker is one-shot: a *genuine* later Google edit propagates.
    ev_real = SyncEvent(system="google", facet="property", incoming_hash="hG2")
    assert should_propagate(session, pair, ev_real, settings).propagate


def test_expired_inflight_does_not_suppress(session, settings):
    settings.inflight_ttl_seconds = -1  # already expired when set
    pair = _pair(session)
    mark_propagated(session, pair, "google", "property", "hG", settings)
    ev = SyncEvent(system="google", facet="property", incoming_hash="hG")
    res = should_propagate(session, pair, ev, settings)
    # Expired marker is swept; the change is treated as real.
    assert res.decision is Decision.PROPAGATE
