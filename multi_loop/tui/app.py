"""The multi-loop agent environment: dashboard, operator chat, settings.

Run with ``multi-loop tui``. The app owns presentation and context assembly;
codex (via :class:`CodexOperatorEngine`) is the swappable brain behind the
chat. A serve loop ticks scheduled missions inside the app and pushes each
finished generation's executive report straight into the room.
"""

from __future__ import annotations

from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Markdown,
    RichLog,
    Select,
    Static,
    TabbedContent,
    TabPane,
)

from ..orchestrator import MissionOrchestrator
from ..reports import render_mission_report
from ..runners import default_runner_registry
from ..scheduler import MissionScheduler
from ..storage import MissionStore
from .engine import CodexOperatorEngine
from .snapshot import build_snapshot

_TICK_INTERVAL_SECONDS = 30.0
_REFRESH_INTERVAL_SECONDS = 5.0


class MultiLoopApp(App):
    """Mission control for the laid-back user."""

    TITLE = "multi-loop"
    SUB_TITLE = "mission control"
    BINDINGS = [("ctrl+q", "quit", "Quit")]
    CSS = """
    #missions { height: 40%; }
    #report-view { height: 60%; border-top: solid $accent; padding: 0 1; }
    #chat-log { height: 1fr; padding: 0 1; }
    #chat-input { dock: bottom; }
    #settings-form { padding: 1 2; }
    #settings-form Input, #settings-form Select { margin-bottom: 1; }
    .settings-row { height: auto; }
    .settings-row Button { margin-right: 2; }
    """

    def __init__(self, root: str | Path = ".multi-loop", *, workdir: str | Path | None = None) -> None:
        super().__init__()
        self.store = MissionStore(root)
        self.orchestrator = MissionOrchestrator(store=self.store)
        self.scheduler = MissionScheduler(store=self.store, orchestrator=self.orchestrator)
        self.engine = CodexOperatorEngine(Path(workdir or Path.cwd()))
        self.selected_mission_id: str | None = None
        self._mission_options: list[tuple[str, str]] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent():
            with TabPane("Dashboard", id="tab-dashboard"):
                yield DataTable(id="missions", cursor_type="row")
                yield VerticalScroll(Markdown("", id="report-view"))
            with TabPane("Chat", id="tab-chat"):
                yield RichLog(id="chat-log", wrap=True, markup=True)
                yield Input(
                    placeholder="Talk to your operator…  (/approve <capability> to grant authority)",
                    id="chat-input",
                )
            with TabPane("Settings", id="tab-settings"):
                with Vertical(id="settings-form"):
                    yield Label("Mission")
                    yield Select([], id="setting-mission", allow_blank=True)
                    yield Label("Schedule (e.g. 'every 30m'; empty clears)")
                    yield Input(id="setting-schedule")
                    yield Label("Runner")
                    yield Select(
                        [(name, name) for name in default_runner_registry().names()],
                        id="setting-runner",
                        allow_blank=True,
                    )
                    with Horizontal(classes="settings-row"):
                        yield Button("Save mission settings", id="save-settings", variant="primary")
                    yield Label("Authority")
                    yield Static("", id="approvals-view")
                    yield Input(placeholder="capability name", id="setting-capability")
                    with Horizontal(classes="settings-row"):
                        yield Button("Approve", id="approve-capability", variant="success")
                        yield Button("Revoke", id="revoke-capability", variant="error")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#missions", DataTable)
        table.add_columns("mission", "statement", "gens", "schedule", "next run")
        self._refresh_dashboard()
        self._chat_write(
            "[bold]operator[/bold]: Console ready. Ask me anything about your missions — "
            "I already have the current state."
        )
        self.set_interval(_REFRESH_INTERVAL_SECONDS, self._refresh_dashboard)
        self.set_interval(_TICK_INTERVAL_SECONDS, self._tick)

    # ------------------------------------------------------------- dashboard

    def _refresh_dashboard(self) -> None:
        missions = sorted(
            self.store.list_missions(), key=lambda mission: mission.updated_at, reverse=True
        )
        table = self.query_one("#missions", DataTable)
        table.clear()
        for mission in missions:
            schedule = mission.schedule
            table.add_row(
                mission.id,
                mission.statement[:60],
                str(len(mission.generations)),
                f"{schedule.display or schedule.expression} [{schedule.state.value}]"
                if schedule
                else "—",
                (schedule.next_run_at or "—")[:19] if schedule else "—",
                key=mission.id,
            )
        if missions and self.selected_mission_id is None:
            self.selected_mission_id = missions[0].id
        self._refresh_report()
        self._refresh_settings(missions)

    def _refresh_report(self) -> None:
        view = self.query_one("#report-view", Markdown)
        if not self.selected_mission_id:
            view.update("_No missions yet. Create one in Chat: just describe what you want._")
            return
        try:
            mission = self.store.load_mission(self.selected_mission_id)
        except Exception:
            return
        view.update(
            render_mission_report(mission, self.store.read_permissions(mission.id))
        )

    def _refresh_settings(self, missions) -> None:
        select = self.query_one("#setting-mission", Select)
        options = [(f"{m.id} — {m.statement[:40]}", m.id) for m in missions]
        if options != self._mission_options:  # avoid clobbering user focus every refresh
            self._mission_options = options
            select.set_options(options)
        if self.selected_mission_id and select.value != self.selected_mission_id:
            select.value = self.selected_mission_id
        if self.selected_mission_id:
            try:
                mission = self.store.load_mission(self.selected_mission_id)
            except Exception:
                return
            grants = (
                "\n".join(f"• {cap} — granted by {who}" for cap, who in sorted(mission.approvals.items()))
                or "none (read-only and local)"
            )
            self.query_one("#approvals-view", Static).update(grants)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.selected_mission_id = str(event.row_key.value)
        self._refresh_report()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "setting-mission" and event.value != Select.BLANK:
            self.selected_mission_id = str(event.value)
            self._refresh_report()

    # ------------------------------------------------------------------ chat

    def _chat_write(self, text: str) -> None:
        self.query_one("#chat-log", RichLog).write(text)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "chat-input":
            return
        message = event.value.strip()
        if not message:
            return
        event.input.value = ""
        self._chat_write(f"[bold cyan]you[/bold cyan]: {message}")
        if message.startswith("/approve ") or message.startswith("/revoke "):
            self._handle_authority_command(message)
            return
        self._chat_write("[dim]operator is working…[/dim]")
        self._operator_turn(message)

    def _handle_authority_command(self, message: str) -> None:
        action, _, capability = message.partition(" ")
        capability = capability.strip()
        if not self.selected_mission_id or not capability:
            self._chat_write("[red]Select a mission and name a capability.[/red]")
            return
        try:
            if action == "/approve":
                self.orchestrator.approve_capability(
                    self.selected_mission_id, capability, approved_by="user"
                )
                self._chat_write(
                    f"[green]Granted[/green] `{capability}` — recorded in the permission ledger."
                )
            else:
                self.orchestrator.revoke_capability(
                    self.selected_mission_id, capability, revoked_by="user"
                )
                self._chat_write(
                    f"[yellow]Revoked[/yellow] `{capability}` — recorded in the permission ledger."
                )
        except Exception as exc:  # surface, never crash the room
            self._chat_write(f"[red]{exc}[/red]")
        self._refresh_dashboard()

    @work(thread=True, exclusive=True, group="operator")
    def _operator_turn(self, message: str) -> None:
        snapshot = build_snapshot(self.store, selected_mission_id=self.selected_mission_id)
        reply = self.engine.turn(message, snapshot=snapshot)
        if reply.ok and reply.text:
            self.call_from_thread(self._chat_write, f"[bold]operator[/bold]: {reply.text}")
        else:
            self.call_from_thread(
                self._chat_write, f"[red]operator error: {reply.error or 'empty reply'}[/red]"
            )
        self.call_from_thread(self._refresh_dashboard)

    # ------------------------------------------------------------ serve loop

    @work(thread=True, exclusive=True, group="tick")
    def _tick(self) -> None:
        try:
            report = self.scheduler.tick()
        except Exception as exc:  # keep the console alive through tick failures
            self.call_from_thread(self._chat_write, f"[red]tick failed: {exc}[/red]")
            return
        for result in report.ticked:
            try:
                mission = self.store.load_mission(result.mission_id)
                rendered = render_mission_report(
                    mission, self.store.read_permissions(mission.id)
                )
            except Exception:
                rendered = ""
            self.call_from_thread(
                self._chat_write,
                f"[bold magenta]mission update[/bold magenta]: {result.mission_id} "
                f"finished generation {result.generation_index} "
                f"({result.last_status or 'unknown'}).",
            )
            if rendered:
                self.call_from_thread(self._chat_write, rendered)
            self.call_from_thread(
                self.notify,
                f"{result.mission_id}: generation {result.generation_index} finished",
            )
        if report.ticked:
            self.call_from_thread(self._refresh_dashboard)

    # -------------------------------------------------------------- settings

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if not self.selected_mission_id:
            self.notify("Select a mission first.", severity="warning")
            return
        try:
            if event.button.id == "save-settings":
                patch: dict[str, object] = {}
                schedule = self.query_one("#setting-schedule", Input).value.strip()
                patch["schedule"] = schedule or None
                runner = self.query_one("#setting-runner", Select).value
                if runner != Select.BLANK:
                    patch["execution_profile"] = {"runner": str(runner)}
                self.orchestrator.configure_mission(
                    self.selected_mission_id, patch, changed_by="user"
                )
                self.notify("Mission settings saved.")
            elif event.button.id in {"approve-capability", "revoke-capability"}:
                capability = self.query_one("#setting-capability", Input).value.strip()
                if not capability:
                    self.notify("Name a capability.", severity="warning")
                    return
                if event.button.id == "approve-capability":
                    self.orchestrator.approve_capability(
                        self.selected_mission_id, capability, approved_by="user"
                    )
                    self.notify(f"Granted {capability}.")
                else:
                    self.orchestrator.revoke_capability(
                        self.selected_mission_id, capability, revoked_by="user"
                    )
                    self.notify(f"Revoked {capability}.")
        except Exception as exc:
            self.notify(str(exc), severity="error")
        self._refresh_dashboard()


def run_tui(root: str | Path = ".multi-loop") -> int:
    """Entry point for ``multi-loop tui``."""
    MultiLoopApp(root).run()
    return 0
