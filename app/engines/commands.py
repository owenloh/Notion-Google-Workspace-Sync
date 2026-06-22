"""Command executor: Google Tasks → relay → Notion, then re-reflect.

Reads pending command tasks, parses each into a relay request, forwards it to the
Alistair Skills API, completes the task with a `✓`/`✗` receipt, and re-reflects the
affected Notion page so the read mirror updates immediately. The same
``execute_one`` powers the synchronous HTTP `/command` endpoint.
"""

from __future__ import annotations

from sqlmodel import Session

from app.config import Settings, get_settings
from app.connectors.google import tasks as gtasks
from app.connectors.relay import RelayClient, RelayResult
from app.engines.command_schema import CommandError, RelayRequest, parse_command
from app.engines.google_mirror import GoogleMirror
from app.engines.notion_source import NotionSource
from app.logging import get_logger

log = get_logger(__name__)


class CommandExecutor:
    def __init__(
        self,
        session: Session,
        notion: NotionSource,
        google: GoogleMirror,
        relay: RelayClient,
        settings: Settings | None = None,
    ):
        self.session = session
        self.notion = notion
        self.google = google
        self.relay = relay
        self.settings = settings or get_settings()

    def execute_one(self, req: RelayRequest) -> RelayResult:
        """Forward one request and re-reflect the affected surface on success."""
        result = self.relay.execute(req)
        if result.ok:
            if req.path == "/api/intray":
                self._refresh_intray()  # MS To-Do command → refresh the _Intray Doc
            elif result.affected_id:
                self._reflect(result.affected_id)
        return result

    def run_pending(self) -> int:
        """Process every pending command task. Returns the number handled."""
        handled = 0
        for task in self.google.pending_commands():
            text = gtasks.command_text(task)
            parsed = parse_command(text, default_path=self.settings.relay_default_path)
            if isinstance(parsed, CommandError):
                receipt = f"{gtasks.RECEIPT_ERR} {parsed.message}"
            else:
                result = self.execute_one(parsed)
                mark = gtasks.RECEIPT_OK if result.ok else gtasks.RECEIPT_ERR
                receipt = f"{mark} {result.summary}"
            self.google.finish_command(task, receipt)
            handled += 1
            log.info("command handled: %s", receipt)
        return handled

    def _reflect(self, page_id: str) -> None:
        """Re-mirror just the affected Notion page (best-effort)."""
        from app.engines.mirror_out import MirrorOut

        try:
            item = self.notion.get_item(page_id)
            MirrorOut(self.session, self.notion, self.google, self.settings).mirror_item(item)
        except Exception:  # noqa: BLE001 — reflection is best-effort; poll will catch up
            log.exception("re-reflect failed for %s", page_id)

    def _refresh_intray(self) -> None:
        """Regenerate the `_Intray` Doc after a MS To-Do command (best-effort)."""
        from app.engines.mirror_out import MirrorOut

        try:
            MirrorOut(self.session, self.notion, self.google, self.settings).refresh_intray()
        except Exception:  # noqa: BLE001 — reflection is best-effort; poll will catch up
            log.exception("intray re-reflect failed")
