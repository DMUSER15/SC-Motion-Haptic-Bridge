"""RSI Launcher / game card: detect, launch (or restart a launcher left open). Opening the launcher
is always safe for the rig: no motion before you are spawned in the game. The launcher is never
closed from here; Wine clean-up does that on request."""
from html import escape as esc

from PySide6 import QtWidgets

from ui_base import Panel as Base

PANEL = {"id": "game", "title": "RSI Launcher / Star Citizen"}
MOTION_WARNING = ("PLATFORM MOVEMENT VALUES HAVE CHANGED in an update applied in this window. Check your SimHub "
                  "Motion settings (Motion tab and Motion haptics) before anyone rides. First run: nobody in the rig "
                  "and motion power OFF, then power ON with nobody in the rig, then a person.")


class Panel(Base):
    def build(self):
        self.info = self.detail()
        self.b_launch = self.button("Start RSI Launcher", self.launch)

    def launch(self):
        why = self.control.game_gate()
        if why:
            QtWidgets.QMessageBox.information(self, "Not now", "The RSI Launcher cannot start:\n- " + "\n- ".join(why))
            return
        snap = self.control.snapshot()
        if snap.get("motion_changed") and QtWidgets.QMessageBox.warning(
                self, "Platform movement values changed", MOTION_WARNING + "\n\nStart the RSI Launcher?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No) != QtWidgets.QMessageBox.Yes:
            return
        if snap["game"]["launcher"] and self.control.platform != "windows" and QtWidgets.QMessageBox.question(
                self, "Restart the RSI Launcher",
                "An RSI Launcher is already open (perhaps without a window). Starting it again closes it and Wine in "
                "the game prefix first (sc-launch.sh does that), then opens a new one. Restart it?") != QtWidgets.QMessageBox.Yes:
            return
        self.control.run_task("game", self.control.launch_game)

    def refresh(self, snap):
        g = snap["game"]
        self.light.set(g["light"])
        self.status.setText(g["text"])
        busy = bool(snap.get("task"))
        if g["game"]:
            self.info.setText("Motion follows the game while the bridge runs.")
        else:
            why = self.control.game_gate(snap=snap)
            notes = self.control.launch_notes(snap)
            if why:
                self.info.setText("Cannot start: " + esc("; ".join(why)))
            elif notes:
                self.info.setText("Note: " + esc("; ".join(notes)))
            else:
                self.info.setText("Ready: motion bridge and SimHub are running.")
        self.b_launch.setEnabled(not busy and not g["game"])
        if self.control.platform == "windows":
            # Windows: starting it again only brings the open launcher to the front
            self.b_launch.setText("Show RSI Launcher" if g["launcher"] else "Start RSI Launcher")
        else:
            self.b_launch.setText("Restart RSI Launcher…" if g["launcher"] else "Start RSI Launcher")
