"""window.py - the control window: cards from the panel registry, the activity log, the update
prompt, and the start-up order (update check -> motion pipeline)."""
import importlib.util
import os
import sys
import threading
import time
from html import escape as esc

from PySide6 import QtCore, QtGui, QtWidgets

import scm_control as C
import ui_base

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(HERE)
REGISTRY = os.path.join(HERE, "panels.json")


class Ctx:
    def __init__(self, control, profile, dev):
        self.control = control
        self.profile = profile
        self.dev = dev
        self.window = None
        self.started = time.time()


def load_registry(dev, profile, path=REGISTRY):
    """See scm_control.load_registry (kept there so --check works without PySide6)."""
    return C.load_registry(dev, profile, path)


def load_panel(entry):
    spec = importlib.util.spec_from_file_location("scpanel_" + entry["id"], entry["path"])
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    meta = dict(entry)
    meta["title"] = getattr(mod, "PANEL", {}).get("title") or entry.get("title") or entry["id"]
    return mod.Panel, meta


class UpdateDialog(QtWidgets.QDialog):
    def __init__(self, recs, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Updates available")
        lay = QtWidgets.QVBoxLayout(self)
        intro = QtWidgets.QLabel("Updates were found for these apps. Choose what to update now; the rest can wait.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.boxes = []
        for r in recs:
            if not r.get("update"):
                continue
            box = QtWidgets.QGroupBox()
            bl = QtWidgets.QVBoxLayout(box)
            cb = QtWidgets.QCheckBox("%s:  %s  →  %s" % (r["title"], r.get("current") or "?", r.get("available") or "?"))
            cb.setChecked(not r.get("blocked"))
            cb.setEnabled(not r.get("blocked"))
            f = cb.font()
            f.setBold(True)
            cb.setFont(f)
            bl.addWidget(cb)
            row = QtWidgets.QHBoxLayout()
            for url, label in ((r.get("url"), "Repository"), (r.get("release_url"), "Release page")):
                if url:
                    row.addWidget(ui_base.link_label(url, "%s: %s" % (label, url.replace("https://", ""))))
            if r.get("changes") or r.get("summary"):
                b = QtWidgets.QPushButton("Show changes…")
                b.setToolTip("The new CHANGELOG sections of %s %s" % (r["title"], r.get("available") or ""))
                b.clicked.connect(lambda _=False, rec=r: ui_base.show_text(
                    self, "Changes: %s %s → %s" % (rec["title"], rec.get("current") or "?", rec.get("available") or "?"),
                    rec.get("changes") or "\n".join(rec.get("summary") or []), mono=False))
                row.addStretch(1)
                row.addWidget(b)
            if row.count():
                bl.addLayout(row)
            if r.get("motion"):
                w = QtWidgets.QLabel("CHANGES PLATFORM MOVEMENT VALUES (%s). You will be asked to type YES." %
                                     ", ".join(r.get("motion_files") or ["release says so"]))
                w.setTextFormat(QtCore.Qt.PlainText)        # file names come from the remote
                w.setWordWrap(True)
                w.setStyleSheet("background:#7f1d1d; color:white; padding:4px; border-radius:4px;")
                bl.addWidget(w)
            text = "\n".join(r.get("summary") or [])
            if r.get("notes"):
                text += ("\n\n" if text else "") + "\n".join(r["notes"])
            if r.get("blocked"):
                text += "\n\nCannot update here: " + r["blocked"]
            if r.get("needs_simhub_closed"):
                text += "\n\nSimHub must be closed for this update (game definition or plugin)."
            t = QtWidgets.QPlainTextEdit(text)
            t.setReadOnly(True)
            t.setMaximumHeight(170)
            bl.addWidget(t)
            lay.addWidget(box)
            self.boxes.append((cb, r))
        bb = QtWidgets.QDialogButtonBox()
        self.b_up = bb.addButton("Update selected", QtWidgets.QDialogButtonBox.AcceptRole)
        bb.addButton("Not now", QtWidgets.QDialogButtonBox.RejectRole)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.resize(720, 200 + 230 * len(self.boxes))

    def chosen(self):
        return [r for cb, r in self.boxes if cb.isChecked()]


CLOSE_CLEAN, CLOSE_SKIP, CLOSE_CANCEL = "clean", "skip", "cancel"
PART_TEXT = {"game": "Game prefix: RSI Launcher, Gamescope and Wine helpers",
             "simhub": "SimHub's prefix: SimHub itself and its Wine helpers"}


def close_cleanup_text(found, parts, settings):
    """-> (text, details) of the close confirmation. Static text: nothing to tick."""
    lines = ["This window cleans up Wine when it closes. Closing now stops the motion bridge and the capture "
             "server, then ends:"]
    for p in parts:
        lines.append("  •  %s (%d process(es))" % (PART_TEXT[p], len(found[p]["end"])))
    if "simhub" in parts and found["simhub"].get("simhub") in ("running", "stuck"):
        if settings.get("simhub_rig_check", True):
            lines.append("\nSimHub drives the motion rig and is closed too. Before you continue: motion power OFF "
                         "and nobody in the rig.")
        else:
            lines.append("\nSimHub drives the motion rig and is closed too (the rig parks by itself after its "
                         "standby time).")
    lines.append("\nCancel keeps this window open and changes nothing.")
    details = []
    for p in parts:
        details.append(PART_TEXT[p] + ":")
        details += ["  pid %-7d %-16s %s" % (pid, name, cmd[:70]) for pid, name, cmd in found[p]["end"][:20]]
    return "\n".join(lines), "\n".join(details)


def ask_close_cleanup(parent, found, parts, settings):
    """The close confirmation (session 34: replaces the rig tick boxes at close).
    -> CLOSE_CLEAN, CLOSE_SKIP (close, no clean-up) or CLOSE_CANCEL (back to the window).
    Cancel is the default and the Escape answer, and closing the box is Cancel too."""
    text, details = close_cleanup_text(found, parts, settings)
    box = QtWidgets.QMessageBox(parent)
    box.setIcon(QtWidgets.QMessageBox.Warning)
    box.setWindowTitle("Close and clean up Wine")
    box.setText(text)
    box.setDetailedText(details)
    b_clean = box.addButton("Clean up and close", QtWidgets.QMessageBox.DestructiveRole)
    b_skip = box.addButton("Close without clean-up", QtWidgets.QMessageBox.AcceptRole)
    b_cancel = box.addButton("Cancel", QtWidgets.QMessageBox.RejectRole)
    box.setDefaultButton(b_cancel)
    box.setEscapeButton(b_cancel)
    box.exec()
    clicked = box.clickedButton()
    if clicked is b_clean:
        return CLOSE_CLEAN
    if clicked is b_skip:
        return CLOSE_SKIP
    return CLOSE_CANCEL


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, ctx, entries, title):
        super().__init__()
        self.ctx = ctx
        ctx.window = self
        self.control = ctx.control
        self.setWindowTitle(title)
        self.cards = []
        self.errors = set()
        self.pending = None
        self.auto_launched = False
        self.restart_after = False
        self.force_close = False          # SIGTERM / log out: close without questions
        self.nseen = 0
        central = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(central)
        head = QtWidgets.QHBoxLayout()
        h = QtWidgets.QLabel("<b style='font-size:15px'>%s</b>" % esc(title))
        head.addWidget(h)
        head.addStretch(1)
        self.task_label = QtWidgets.QLabel("")
        head.addWidget(self.task_label)
        sb = QtWidgets.QToolButton()
        sb.setText("Settings")
        sb.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        sb.setMenu(self._settings_menu())
        head.addWidget(sb)
        outer.addLayout(head)
        split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QtWidgets.QWidget()
        self.grid = QtWidgets.QVBoxLayout(inner)
        self.grid.setSpacing(8)
        for entry in entries:
            try:
                cls, meta = load_panel(entry)
                card = cls(ctx, meta)
            except Exception as e:  # noqa: BLE001  one broken card never stops the window
                self.control.say("card %s not loaded (%s: %s)" % (entry["id"], type(e).__name__, e))
                continue
            self.grid.addWidget(card)
            self.cards.append(card)
        self.grid.addStretch(1)
        scroll.setWidget(inner)
        split.addWidget(scroll)
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(3000)
        self.log.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
        split.addWidget(self.log)
        split.setSizes([620, 200])
        outer.addWidget(split, 1)
        self.setCentralWidget(central)
        self.resize(820, 940)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(500)

    def _settings_menu(self):
        m = QtWidgets.QMenu(self)
        s = self.control.settings
        items = [("check_updates", "Check for updates when the window opens"),
                 ("autostart", "Start the motion bridge when the window opens"),
                 ("start_simhub", "Start SimHub after the bridge self-test"),
                 ("start_launcher", "Start the RSI Launcher after the start-up"),
                 ("wine_cleanup_on_close", "Clean up Wine (game, SimHub) when this window closes"),
                 ("simhub_rig_check", "Rig checklist before SimHub is closed (off: Yes/No only)")]
        if self.control.platform == "windows":       # no Wine on Windows
            items = [x for x in items if x[0] != "wine_cleanup_on_close"]
        for key, text in items:
            a = m.addAction(text)
            a.setCheckable(True)
            a.setChecked(bool(s.get(key)))
            a.toggled.connect(lambda on, k=key: self._set(k, on))
        return m

    def _set(self, key, on):
        self.control.settings[key] = on
        try:
            C.save_settings(self.control.settings)
        except OSError as e:
            self.control.say("settings not saved (%s)" % e)

    # ---- start-up order
    def startup(self):
        self.control.say("control window started (%s)" % ("developer mode" if self.ctx.dev else "normal mode"))
        if self.control.settings.get("check_updates", True) and os.environ.get("SC_UPDATE_CHECK") != "0":
            self.check_updates(prompt=True, then_start=True)
        else:
            self.autostart()

    def autostart(self):
        if self.control.settings.get("autostart", True):
            if self.control.run_task("start", self.control.start_pipeline):
                self.pending = ("start", False, False)

    def auto_launch(self):
        """After the start-up (setting "start_launcher", once per window): the RSI Launcher, like the
        terminal launcher does. A launcher left open earlier is restarted by sc-launch.sh itself."""
        if self.auto_launched or not self.control.settings.get("start_launcher", True):
            return
        self.auto_launched = True
        snap = self.control.snapshot()
        if snap["game"]["game"]:
            return
        if snap["pipeline"] != "running":
            self.control.say("RSI Launcher not started automatically: the motion pipeline is not running "
                             "(fix that, or press Launch to play without motion)")
            return
        if snap.get("motion_changed"):
            self.control.say("RSI Launcher not started automatically: an update changed platform movement values. "
                             "Check SimHub's Motion settings, then press Launch.")
            return
        if self.control.game_gate(snap=snap):
            self.control.say("RSI Launcher not started automatically: " + "; ".join(self.control.game_gate(snap=snap)))
            return
        self.control.say("starting the RSI Launcher (setting: start it after the start-up)")
        self.control.run_task("game", self.control.launch_game)

    def check_updates(self, prompt=True, then_start=False):
        if self.control.run_task("update-check", self.control.check_updates):
            self.pending = ("update-check", prompt, then_start)
        elif then_start:
            self.autostart()

    def show_update_dialog(self, then_start=False, only=None):
        """only: app ids to offer (the MFD card's Update MFD button offers just the MFD)."""
        recs = self.control.snapshot().get("updates") or []
        full = {r["id"]: r for r in self.control._updates}
        recs = [full.get(r["id"], r) for r in recs if only is None or r["id"] in only]
        if not any(r.get("update") for r in recs):
            if then_start:
                self.autostart()
            return
        d = UpdateDialog(recs, self)
        if d.exec() != QtWidgets.QDialog.Accepted or not d.chosen():
            self.control.say("updates not applied now")
            if then_start:
                self.autostart()
            return
        chosen = d.chosen()
        motion_ok = True
        if any(r.get("motion") for r in chosen):
            motion_ok = ui_base.type_yes(self, "Platform movement values change",
                                         "This update CHANGES PLATFORM MOVEMENT VALUES. Confirm you will check your "
                                         "SimHub Motion settings before anyone rides (first run: nobody in the rig, "
                                         "motion power OFF; then ON with nobody in the rig; then a person).")
        chosen = [r for r in chosen if motion_ok or not r.get("motion")]
        if not chosen:
            self.control.say("updates not applied (YES was not typed)")
            if then_start:
                self.autostart()
            return
        snap = self.control.snapshot()
        # Refuse here, before anything is stopped: the update itself would be refused anyway, and
        # stopping the bridge while flying ends motion and shaker effects.
        if snap["game"]["game"] or self.control.sc.scan(self.control.sc.GAME_EXE):
            QtWidgets.QMessageBox.information(self, "Updates", "Star Citizen is running: update after the game. "
                                              "Nothing was stopped or changed.")
            self.control.say("updates not applied: Star Citizen is running")
            return
        if self.control.busy():
            self.control.say("updates not applied now: %s is still running; try again when it has finished" % snap.get("task"))
            return
        # Only the bridge and the MFD add-on run from the updated files; the THANOS tool does not.
        was_running = snap["pipeline"] in ("running", "starting", "needs_decision") and any(
            r["id"] in ("bridge", "mfd") for r in chosen)
        ids = [r["id"] for r in chosen]

        def work():
            if was_running:
                self.control.stop_pipeline("stopped for the update")
            self.restart_after = self.control.apply_updates(ids, motion_confirmed=motion_ok)
        if self.control.run_task("update", work):
            self.pending = ("update", False, then_start or was_running)

    def install_mfd(self):
        """The MFD card's Install MFD button: where it comes from (with a link), what's new, then install."""
        offer = self.control.mfd_offer()
        if not offer["source"]:
            QtWidgets.QMessageBox.information(self, "Install MFD", offer["problem"])
            return
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            info = self.control.mfd_install_info()
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        if offer["source"] == "clone":
            where = "your clone %s (setting mfd_source)" % offer["where"]
        else:
            where = "the newest release (%s) of github.com/%s" % (info.get("version") or "?", offer["where"])
        d = QtWidgets.QDialog(self)
        d.setWindowTitle("Install MFD")
        lay = QtWidgets.QVBoxLayout(d)
        t = QtWidgets.QLabel("Install the MFD add-on from %s?\n\n"
                             "Every file is checked against the release's release.json (sha256) before anything is kept. "
                             "Then the MFD's own installer runs (as you): it adds its settings, menu entries and its "
                             "telemetry tap, and lists the Python modules and udev rules it needs. Motion, SimHub and the "
                             "bridge are not changed. Install only add-ons you trust." % where)
        t.setTextFormat(QtCore.Qt.PlainText)
        t.setWordWrap(True)
        lay.addWidget(t)
        for url, label in ((info.get("url"), "Repository"), (info.get("release_url"), "Release page")):
            if url:
                lay.addWidget(ui_base.link_label(url, "%s: %s" % (label, url.replace("https://", ""))))
        if info.get("problem"):
            p = QtWidgets.QLabel("Could not read the release notes: %s" % info["problem"])
            p.setTextFormat(QtCore.Qt.PlainText)
            p.setWordWrap(True)
            lay.addWidget(p)
        bb = QtWidgets.QDialogButtonBox()
        if info.get("changes"):
            b = bb.addButton("What's new…", QtWidgets.QDialogButtonBox.HelpRole)
            b.clicked.connect(lambda: ui_base.show_text(d, "MFD %s: changes" % (info.get("version") or ""),
                                                        info["changes"], mono=False))
        bb.addButton("Install", QtWidgets.QDialogButtonBox.AcceptRole)
        bb.addButton("Cancel", QtWidgets.QDialogButtonBox.RejectRole)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        lay.addWidget(bb)
        d.setMinimumWidth(560)
        if d.exec() != QtWidgets.QDialog.Accepted:
            self.control.say("MFD not installed (cancelled)")
            return
        self.control.run_task("mfd-install", self.control.mfd_install)

    def _pending_done(self):
        kind, prompt, then_start = self.pending
        self.pending = None
        if kind == "start":
            self.auto_launch()
            return
        if kind == "update-check":
            if prompt:
                self.show_update_dialog(then_start=then_start)
            elif then_start:
                self.autostart()
        elif kind == "update":
            if self.restart_after:
                QtWidgets.QMessageBox.information(self, "Updated", "The motion bridge was updated. The window restarts now "
                                                  "with the new version.")
                self.restart()
                return
            if then_start:
                self.autostart()

    def restart(self):
        for card in self.cards:
            card.on_close()
        self.control.shutdown(stop_pipeline=True)
        EL = getattr(self.control.sc, "EL", None)
        if EL is not None and EL.current() is not None:
            EL.finish(0)
        if os.name == "nt":
            # Windows: os.execv starts the new copy while this one is still running; start it
            # detached instead and leave (as scm_update._restart does for the terminal launcher)
            import subprocess
            subprocess.Popen([sys.executable] + sys.argv, creationflags=0x00000008 | 0x00000200,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            os._exit(0)
        os.execv(sys.executable, [sys.executable] + sys.argv)

    # ---- refresh
    def tick(self):
        snap = self.control.snapshot()
        for card in self.cards:
            try:
                vis = card.visible_for(snap)
                card.setVisible(vis)
                if vis:
                    card.refresh(snap)
            except Exception as e:  # noqa: BLE001
                key = (card.meta["id"], type(e).__name__)
                if key not in self.errors:
                    self.errors.add(key)
                    self.control.say("card %s: %s: %s" % (card.meta["id"], type(e).__name__, e))
        n, lines = self.control.lines_since(self.nseen)
        self.nseen = n
        for line in lines:
            self.log.appendPlainText(line)
        self.task_label.setText(("⏳ " + snap["task"]) if snap.get("task") else "")
        if self.pending and not self.control.busy():
            self._pending_done()

    def closeEvent(self, ev):
        snap = self.control.snapshot()
        if snap["game"]["game"] and not self.force_close:
            if QtWidgets.QMessageBox.question(self, "Close", "Star Citizen is running. Closing this window stops the "
                                              "motion bridge (motion and shaker effects end). Close anyway?") != QtWidgets.QMessageBox.Yes:
                ev.ignore()
                return
        # Asked before anything is stopped, so Cancel leaves the window and the pipeline as they are.
        parts = self.close_cleanup_parts() if not self.force_close else []
        if parts is None:
            ev.ignore()
            return
        for card in self.cards:
            if not card.on_close() and not self.force_close:
                ev.ignore()
                return
        self.timer.stop()
        self.control.say("closing: stopping the bridge and capture server; SimHub and the game are left as they are"
                         if not parts else "closing: stopping the bridge and capture server, then cleaning up Wine (%s)"
                         % ", ".join(parts))
        self.control.shutdown(stop_pipeline=True)
        if parts:
            self.run_blocking("Cleaning up Wine…", self.control.wine_cleanup, parts)
        ev.accept()

    def close_cleanup_parts(self):
        """Option "clean up Wine when this window closes": -> the parts to clean after closing
        ([] = none), or None = the close was cancelled (back to the window, nothing stopped).
        One static warning (ask_close_cleanup) when there is something to end; it names SimHub
        and the rig check when SimHub would be closed. Never the game prefix while the game runs."""
        if not self.control.settings.get("wine_cleanup_on_close") or not self.control.wine_supported():
            return []
        found = self.control.wine_scan()
        parts = [p for p in ("game", "simhub") if found.get(p) and found[p]["end"] and not found[p].get("blocked")]
        for p in ("game", "simhub"):
            if found.get(p, {}).get("blocked"):
                self.control.say("Wine clean-up of the %s skipped: %s" % (p, found[p]["blocked"]))
        if not parts:
            return []
        answer = ask_close_cleanup(self, found, parts, self.control.settings)
        if answer == CLOSE_CANCEL:
            self.control.say("close cancelled: the window stays open")
            return None
        if answer == CLOSE_SKIP:
            self.control.say("closing without the Wine clean-up")
            return []
        return parts

    def run_blocking(self, text, fn, *args):
        """Run fn(*args) in a thread while the window shows a busy note (used while closing,
        when no task may start any more)."""
        box = QtWidgets.QProgressDialog(text, None, 0, 0, self)
        box.setWindowTitle("Closing")
        box.setMinimumDuration(0)
        box.show()
        t = threading.Thread(target=fn, args=args, daemon=True)
        t.start()
        while t.is_alive():
            QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 50)
            t.join(0.05)
        box.close()
