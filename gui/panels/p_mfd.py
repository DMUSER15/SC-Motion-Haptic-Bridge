"""MFD add-on card (shown only when the add-on is installed): status, Restart MFD, which surfaces
start, and the configurator.

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

    def _info(self):
        now = time.time()
        if self.info_cache is None or now - self.t_info > 15:   # picks up an install / update made meanwhile
            self.info_cache = self.control.mfd_info()
            self.t_info = now
        return self.info_cache

    def visible_for(self, snap):
        return bool(self._info().get("installed"))

    def restart(self):
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
        info = self._info()
        if not info.get("installed"):
            return
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
        self.b_restart.setEnabled(bool(running) or (running is False and snap.get("pipeline") == "running"))
