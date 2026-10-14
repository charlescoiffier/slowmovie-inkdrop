"""Application Textual : cinq onglets, tout au clavier."""
from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
)

from ..cli import format_status
from ..config import ConfigError, Paths, Settings
from ..control import ServiceNotRunning, send_command
from . import model
from .theme import GALAXY

STATUS_LABELS = {"ready": "prêt", "pending": "copie en cours…", "error": "illisible"}
HELP = ("1-5 onglets · Espace pause/reprise · s passer · Entrée valider · q quitter\n"
        "Films : Entrée lancer, z remettre à zéro, d supprimer, a ajouter\n"
        "File : x retirer, c vider · Réglages : Entrée modifier")


class Prompt(ModalScreen[str | None]):
    """Saisie d'une valeur ; Échap annule."""

    BINDINGS = [Binding("escape", "dismiss(None)", "Annuler")]

    def __init__(self, question: str, value: str = ""):
        super().__init__()
        self.question, self.value = question, value

    def compose(self) -> ComposeResult:
        yield Label(self.question)
        yield Input(value=self.value, id="prompt-input")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)


class SlowMovieApp(App):
    TITLE = "SlowMovie-Inkdrop"
    CSS = "Prompt { align: center middle; } Prompt > * { width: 70%; } #player-info { padding: 1 2; }"
    BINDINGS = [
        Binding("1", "tab('player')", "Lecteur"), Binding("2", "tab('films')", "Films"),
        Binding("3", "tab('queue')", "File"), Binding("4", "tab('settings')", "Réglages"),
        Binding("5", "tab('log')", "Journal"),
        Binding("space", "toggle_pause", "Pause"), Binding("s", "send('skip')", "Passer"),
        Binding("z", "reset_film", "Remettre à zéro", show=False),
        Binding("d", "delete_film", "Supprimer", show=False),
        Binding("a", "how_to_add", "Ajouter", show=False),
        Binding("x", "queue_remove", "Retirer", show=False),
        Binding("c", "send('queue_clear')", "Vider", show=False),
        Binding("question_mark", "help", "Aide"), Binding("q", "quit", "Quitter"),
    ]

    def __init__(self, paths: Paths):
        super().__init__()
        self.paths = paths
        self.state: dict | None = None
        self.player_text = ""

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(initial="player"):
            with TabPane("Lecteur", id="player"):
                yield Static("", id="player-info")
            with TabPane("Films", id="films"):
                yield DataTable(id="films-table", cursor_type="row")
            with TabPane("File", id="queue"):
                yield DataTable(id="queue-table", cursor_type="row")
            with TabPane("Réglages", id="settings"):
                yield DataTable(id="settings-table", cursor_type="row")
            with TabPane("Journal", id="log"):
                yield RichLog(id="log-view", wrap=True)
        yield Footer()

    def on_mount(self) -> None:
        self.register_theme(GALAXY)
        self.theme = "galaxy"
        self.query_one("#films-table", DataTable).add_columns("Film", "État", "Progression")
        self.query_one("#queue-table", DataTable).add_columns("Image", "Durée")
        self.query_one("#settings-table", DataTable).add_columns("Réglage", "Valeur")
        self.set_interval(2.0, self.refresh_data)
        self.refresh_data()

    # -- rafraîchissement ------------------------------------------------
    def refresh_data(self) -> None:
        self.state = model.read_state(self.paths)
        self.player_text = format_status(self.state)
        self.query_one("#player-info", Static).update(self.player_text)
        self._fill("#films-table", [
            (r["name"], (r["name"], STATUS_LABELS.get(r["status"], r["status"]),
                         f"{r['percent']} %" if r["percent"] is not None else r["error"]))
            for r in model.film_rows(self.paths)])
        self._fill("#queue-table", [
            (i["id"], (i["id"][:8], f"{i['duration'] // 60} min"))
            for i in (self.state or {}).get("queue", [])])
        settings = Settings.load(self.paths.conf)
        self._fill("#settings-table", [
            (name, (label, str(getattr(settings, name)))) for name, label, _ in model.SETTING_FIELDS])
        log = self.query_one("#log-view", RichLog)
        log.clear()
        log.write(model.tail_log(self.paths))

    def _fill(self, table_id: str, rows: list[tuple[str, tuple]]) -> None:
        """Remplit une table en gardant la ligne sélectionnée (rafraîchissement toutes les 2 s)."""
        table = self.query_one(table_id, DataTable)
        row = table.cursor_row
        table.clear()
        for key, cells in rows:
            table.add_row(*cells, key=key)
        if table.row_count:
            table.move_cursor(row=min(row, table.row_count - 1))

    # -- actions ---------------------------------------------------------
    def _send(self, **command) -> dict | None:
        try:
            reply = send_command(self.paths.socket, command, timeout=25)
        except ServiceNotRunning as exc:
            self.notify(str(exc), severity="error")
            return None
        except OSError as exc:
            self.notify(f"Communication impossible : {exc}", severity="error")
            return None
        if not reply.get("ok"):
            self.notify(reply.get("error", "échec"), severity="error")
        return reply

    def action_tab(self, tab: str) -> None:
        self.query_one(TabbedContent).active = tab
        table = {"films": "#films-table", "queue": "#queue-table",
                 "settings": "#settings-table"}.get(tab)
        if table:
            self.query_one(table, DataTable).focus()      # Entrée agit sur la table de l'onglet

    def action_send(self, name: str) -> None:
        self._send(cmd=name)
        self.refresh_data()

    def action_toggle_pause(self) -> None:
        paused = bool((self.state or {}).get("paused"))
        self._send(cmd="resume" if paused else "pause")
        self.refresh_data()

    def action_help(self) -> None:
        self.notify(HELP, timeout=12)

    def action_how_to_add(self) -> None:
        self.notify("Depuis le Mac : slowmovie add film.mp4 — ou glissez le film dans le partage "
                    "réseau « SlowMovie » (dossier Videos).", timeout=12)

    def _selected_key(self, table_id: str) -> str | None:
        table = self.query_one(table_id, DataTable)
        if table.row_count == 0:
            return None
        return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value

    def action_queue_remove(self) -> None:
        key = self._selected_key("#queue-table")
        if key:
            self._send(cmd="queue_remove", id=key)
            self.refresh_data()

    def action_reset_film(self) -> None:
        key = self._selected_key("#films-table")
        if key:
            self._send(cmd="reset", video=key)
            self.refresh_data()

    def action_delete_film(self) -> None:
        key = self._selected_key("#films-table")
        if not key:
            return

        def confirmed(answer: str | None) -> None:
            if (answer or "").strip().lower() in ("o", "oui"):
                settings = Settings.load(self.paths.conf)
                target = self.paths.videos_dir(settings) / key
                try:
                    target.unlink()
                    self.notify(f"{key} supprimé.")
                except OSError as exc:
                    self.notify(f"Suppression impossible : {exc}", severity="error")
                self.refresh_data()

        self.push_screen(Prompt(f"Supprimer définitivement {key} ? (oui/non)"), confirmed)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        table_id = event.data_table.id
        key = event.row_key.value
        if table_id == "films-table":
            self._send(cmd="play", video=key)
            self.refresh_data()
        elif table_id == "settings-table":
            self._edit_setting(key)

    def _edit_setting(self, name: str) -> None:
        settings = Settings.load(self.paths.conf)
        kind = next(k for n, _, k in model.SETTING_FIELDS if n == name)
        label = next(text for n, text, _ in model.SETTING_FIELDS if n == name)
        current = getattr(settings, name)
        if kind == "bool":
            self._apply_setting(settings, name, not current)
            return

        def entered(value: str | None) -> None:
            if value is None:
                return
            try:
                converted = {"int": int, "float": float, "str": str}[kind](value.strip())
            except ValueError:
                self.notify("Valeur invalide.", severity="error")
                return
            self._apply_setting(settings, name, converted)

        self.push_screen(Prompt(label, str(current)), entered)

    def _apply_setting(self, settings: Settings, name: str, value) -> None:
        setattr(settings, name, value)
        try:
            settings.save(self.paths.conf)
        except ConfigError as exc:
            self.notify(str(exc), severity="error")
            return
        self._send(cmd="reload")
        self.refresh_data()
