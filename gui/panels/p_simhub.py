"""SimHub card: running / orphan process, start, end the orphan and start again."""
from html import escape as esc

import ui_base
from ui_base import Panel as Base

PANEL = {"id": "simhub", "title": "SimHub"}


class Panel(Base):
    def build(self):
        self.info = self.detail()
        self.b_start = self.button("Start SimHub", lambda: self.control.run_task("simhub", self.control.start_simhub))
        self.b_orphan = self.button("End orphan and restart…", self.orphan,
                                    "The SimHub process has lost its Wine server: it has no window and does nothing. "
                                    "This ends it and starts SimHub fresh.")

    def orphan(self):
        ok = ui_base.confirm_end_simhub(
            self, self.control.settings,
            "SimHub is listed as running, but its Wine server is gone, so it has no window and cannot drive the rig. "
            "Ending it and starting SimHub again reconnects the motion controller.",
            ok_text="End it and start SimHub")
        if ok:
            self.control.run_task("simhub-restart", self.control.end_orphan_simhub, True)

    def refresh(self, snap):
        s = snap["simhub"]
        self.light.set(s["light"])
        self.status.setText(s["text"])
        game_step = [t for n, st, t in snap["steps"] if n == "SimHub game"]
        self.info.setText(esc("Active game in SimHub: " + game_step[-1]) if game_step else "")
        busy = bool(snap.get("task"))
        self.b_start.setVisible(s["state"] == "none")
        self.b_start.setEnabled(not busy)
        self.b_orphan.setVisible(s["state"] == "stuck")
        self.b_orphan.setEnabled(not busy)
