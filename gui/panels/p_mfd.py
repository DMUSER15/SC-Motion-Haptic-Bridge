"""MFD add-on card: status, Restart MFD, which surfaces start, the configurator, and Install MFD /
Update MFD. Shown when the add-on is installed or can be installed (a clone at the mfd_source setting or
the catalog entry in addons/catalog.json).

Install MFD (not installed): downloads the newest release of the catalog's repository (or installs your
clone), checks every file against its release.json and runs the MFD's own installer, after you confirm.
The button reads UPDATE MFD when the update check found a newer release; it opens the update dialog
for the MFD only (same checks and rollback as the Updates card).

The MFD only receives a copy of the telemetry from the bridge; nothing on this card stops or
restarts motion, SimHub or the bridge. Restart MFD asks a running MFD to restart its surfaces in
place (same process, port and virtual keyboard) or starts it again if it stopped while motion runs.
The surface boxes switch surfaces on/off in the MFD's own settings (sc_mfd.py surfaces --set); a
running MFD follows within a few seconds.
"""
import time

from PySide6 import QtWidgets

from ui_base import Panel as Base

PANEL = {"id": "mfd", "title": "MFD add-on"}


class Panel(Base):
    def build(self):
        self.info_cache = None
        self.t_info = 0.0
        self.surf_rows = None
        self.t_surf = 0.0
        self.boxes = {}
        self.surf_area = QtWidgets.QWidget()
        self.surf_lay = QtWidgets.QHBoxLayout(self.surf_area)
        self.surf_lay.setContentsMargins(0, 0, 0, 0)
        self.surf_lay.setSpacing(10)
        self.surf_area.setToolTip("Surfaces the MFD starts: a Stream Deck or a window on a monitor / touch screen. "
                                  "A running MFD follows a change within a few seconds; motion is not touched.")
        self.body.addWidget(self.surf_area)
        self.b_restart = self.button("Restart MFD", self.restart,
                                     "Restart the MFD's surfaces (Stream Deck, windows) without touching motion, SimHub or "
                                     "the bridge. Use it after closing a window by hand or when the deck shows nothing.")
        self.b_conf = self.button("MFD configurator", self.control.open_mfd_configurator)
        self.b_inst = self.button("Install MFD", self.install_or_update)
        self.offer_cache = None
        self.t_offer = 0.0

    def _info(self):
        now = time.time()
        if self.info_cache is None or now - self.t_info > 15:   # picks up an install / update made meanwhile
            self.info_cache = self.control.mfd_info()
            self.t_info = now
        return self.info_cache

    def _offer(self):
        now = time.time()
        if self.offer_cache is None or now - self.t_offer > 15:
            self.offer_cache = self.control.mfd_offer()
            self.t_offer = now
        return self.offer_cache

    def visible_for(self, snap):
        return bool(self._info().get("installed")) or bool(self._offer().get("source"))

    @staticmethod
    def _update_rec(snap):
        return next((r for r in (snap.get("updates") or []) if r.get("id") == "mfd"), None)

    def install_or_update(self):
        snap = self.control.snapshot()
        if not self._info().get("installed"):
            self.ctx.window.install_mfd()
        elif (self._update_rec(snap) or {}).get("update"):
            self.ctx.window.show_update_dialog(only=["mfd"])
        self.info_cache = None          # pick up the new version at the next refresh

    def _button_state(self, snap, info):
        """-> (text, enabled, tooltip) of the Install / Update button."""
        busy = snap.get("task")
        if not info.get("installed"):
            offer = self._offer()
            if not offer.get("source"):
                return "Install MFD", False, offer.get("problem") or "no MFD source"
            where = ("your clone " + offer["where"]) if offer["source"] == "clone" else "github.com/%s (newest release)" % offer["where"]
            return "Install MFD", not busy, "Install the MFD add-on from %s. You are asked first." % where
        rec = self._update_rec(snap)
        if rec is None:
            if snap.get("updates") is None:
                return "Update MFD", False, "not checked yet (Updates card: Check now)"
            return "Update MFD", False, "the last update check has no MFD record (Updates card: Check now)"
        if rec.get("update"):
            if rec.get("blocked"):
                return "UPDATE MFD", False, "MFD %s is out but cannot be installed: %s" % (rec.get("available"), rec["blocked"])
            return "UPDATE MFD", not busy, "MFD %s -> %s: opens the update dialog for the MFD" % (rec.get("current"), rec.get("available"))
        if rec.get("problem"):
            return "Update MFD", False, "could not check: %s" % rec["problem"]
        return "MFD up to date", False, "MFD %s is the newest release" % (rec.get("current") or info.get("version", "?"))

    def restart(self):
        if self.control.snapshot().get("mfd_needs_bridge"):
            self.control.run_task("mfd-connect", self.control.restart_bridge_for_addons, "Connect MFD")
        else:
            self.control.mfd_restart()

    def _surfaces(self, force=False):
        now = time.time()
        if force or self.surf_rows is None or now - self.t_surf > 20:   # the configurator may change them
            self.t_surf = now
            rows = self.control.mfd_surfaces()
            if rows is not None and rows != self.surf_rows:
                self.surf_rows = rows
                self._build_boxes()

    def _build_boxes(self):
        while self.surf_lay.count():
            w = self.surf_lay.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        self.boxes = {}
        self.surf_lay.addWidget(QtWidgets.QLabel("Start:"))
        for r in self.surf_rows or []:
            cb = QtWidgets.QCheckBox("%s (%s)" % (r["name"], "Stream Deck" if r.get("type") == "deck" else "window"))
            cb.setChecked(bool(r.get("enabled")))
            cb.toggled.connect(lambda on, name=r["name"]: self._toggle(name, on))
            self.surf_lay.addWidget(cb)
            self.boxes[r["name"]] = cb
        self.surf_lay.addStretch(1)

    def _toggle(self, name, on):
        if not self.control.mfd_set_surface(name, on):
            self._surfaces(force=True)
            return
        for r in self.surf_rows or []:
            if r["name"] == name:
                r["enabled"] = on

    def refresh(self, snap):
        task = snap.get("task")
        if getattr(self, "last_task", None) and not task:     # an install / update just finished: read again
            self.info_cache = self.offer_cache = None
        self.last_task = task
        info = self._info()
        text, on, tip = self._button_state(snap, info)
        self.b_inst.setText(text)
        self.b_inst.setEnabled(on)
        self.b_inst.setToolTip(tip)
        f = self.b_inst.font()
        f.setBold(text == "UPDATE MFD")
        self.b_inst.setFont(f)
        if not info.get("installed"):
            self.light.set("off")
            self.status.setText("MFD add-on not installed. Install MFD adds it (Stream Deck / touch-screen display of "
                                "the telemetry; it never changes motion).")
            self.surf_area.setVisible(False)
            self.b_conf.setEnabled(False)
            self.b_restart.setEnabled(False)
            return
        self.surf_area.setVisible(True)
        self._surfaces()
        running = dict(snap["addons"]).get("mfd")
        if running is None:
            self.light.set("off")
            self.status.setText("MFD %s installed; it starts with the motion bridge" % info.get("version", "?"))
        elif running:
            self.light.set("green")
            self.status.setText("MFD %s running" % info.get("version", "?"))
        else:
            self.light.set("red")
            self.status.setText("MFD %s has STOPPED (its log: ~/lm_capture/launch/addon_mfd.log); Restart MFD starts it "
                                "again without touching motion" % info.get("version", "?"))
        self.b_conf.setEnabled(bool(info.get("configurator")))
        if snap.get("mfd_needs_bridge") and snap.get("pipeline") == "running":
            # installed while Star Citizen runs: the bridge must restart to send the MFD its copy (motion pauses ~2 s)
            self.b_restart.setText("Connect MFD")
            self.b_restart.setToolTip("Restart the bridge so it sends the new MFD its copy of the telemetry. The capture "
                                      "server keeps running (the game stays connected); motion pauses for about two seconds.")
            self.b_restart.setEnabled(not snap.get("task"))
            self.light.set("amber")
            self.status.setText("MFD %s installed: press Connect MFD when it suits you (motion pauses about two seconds)"
                                % info.get("version", "?"))
            f = self.b_restart.font()
            f.setBold(True)
            self.b_restart.setFont(f)
            return
        if self.b_restart.text() != "Restart MFD":
            self.b_restart.setText("Restart MFD")
            f = self.b_restart.font()
            f.setBold(False)
            self.b_restart.setFont(f)
        self.b_restart.setEnabled(bool(running) or (running is False and snap.get("pipeline") == "running"))
