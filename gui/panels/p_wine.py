"""Wine clean-up card: end Wine left over in the game prefix (RSI Launcher, Gamescope, Wine
helpers) and/or SimHub's prefix, on a button press or (option) when this window closes.

Nothing closes Wine by itself: the launcher stays open after the game until you close it. The
work is done by sc_start.py (wine_leftovers / wine_cleanup), shared with
python3 ~/sc_start.py cleanup. SimHub drives the motion rig, so ending it needs the rig check
(motion power OFF, nobody in the rig) unless that is switched off (then a Yes/No question). The game
prefix is never touched while Star Citizen runs.
"""
import time

from PySide6 import QtWidgets

import ui_base
from ui_base import Panel as Base

PANEL = {"id": "wine", "title": "Wine clean-up"}
LABELS = {"game": "Game prefix: RSI Launcher, Gamescope and Wine helpers",
          "simhub": "SimHub's prefix: SimHub itself and its Wine helpers"}


def needs_rig_check(info):
    """Ending this part ends a SimHub process (running or stuck)."""
    return info.get("simhub") in ("running", "stuck")


def describe(part, info):
    if info.get("blocked"):
        return "%s\n  not now: %s" % (LABELS[part], info["blocked"])
    lines = [LABELS[part], "  %d process(es) to end" % len(info["end"])]
    for pid, name, cmd in info["end"][:12]:
        lines.append("  pid %-7d %-16s %s" % (pid, name, cmd[:70]))
    if len(info["end"]) > 12:
        lines.append("  … and %d more" % (len(info["end"]) - 12))
    for pid, name, _cmd in info.get("keep", []):
        lines.append("  kept: pid %d %s (a shell)" % (pid, name))
    return "\n".join(lines)


def ask_parts(parent, found, settings=None):
    """The clean-up dialog. -> list of parts to clean (SimHub confirmed), or [] for nothing."""
    d = QtWidgets.QDialog(parent)
    d.setWindowTitle("Clean up Wine")
    lay = QtWidgets.QVBoxLayout(d)
    intro = QtWidgets.QLabel("Ends Wine processes left over by the game or SimHub. Untick what should stay.")
    intro.setWordWrap(True)
    lay.addWidget(intro)
    boxes = {}
    for part in ("game", "simhub"):
        info = found.get(part) or {"end": [], "keep": []}
        cb = QtWidgets.QCheckBox(LABELS[part])
        usable = bool(info["end"]) and not info.get("blocked")
        cb.setEnabled(usable)
        # SimHub that works normally is not ended unless you tick it; leftovers are pre-ticked
        cb.setChecked(usable and not (part == "simhub" and info.get("simhub") == "running"))
        lay.addWidget(cb)
        txt = QtWidgets.QPlainTextEdit(describe(part, info) if (info["end"] or info.get("blocked")) else "  nothing left over")
        txt.setReadOnly(True)
        txt.setMaximumHeight(120)
        lay.addWidget(txt)
        boxes[part] = cb
    bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Cancel)
    ok = bb.addButton("Clean up", QtWidgets.QDialogButtonBox.AcceptRole)
    ok.setEnabled(any(b.isChecked() for b in boxes.values()))
    for b in boxes.values():
        b.toggled.connect(lambda _=None: ok.setEnabled(any(x.isChecked() for x in boxes.values())))
    bb.accepted.connect(d.accept)
    bb.rejected.connect(d.reject)
    lay.addWidget(bb)
    d.setMinimumWidth(560)
    if d.exec() != QtWidgets.QDialog.Accepted:
        return []
    parts = [p for p, b in boxes.items() if b.isChecked()]
    if "simhub" in parts and needs_rig_check(found["simhub"]):
        if not ui_base.confirm_end_simhub(parent, settings or {}, "SimHub drives the motion rig and is closed now."):
            parts.remove("simhub")
    return parts


class Panel(Base):
    def build(self):
        self.info = self.detail(rich=False)
        self.info.setText("Nothing closes Wine by itself: after the game the RSI Launcher stays open until you "
                          "close it. Use this for a launcher, Gamescope or SimHub that will not close.")
        self.on_close_box = QtWidgets.QCheckBox("Also clean up when this window closes (asks first; Cancel keeps the window open)")
        self.on_close_box.toggled.connect(lambda on: self.ctx.window._set("wine_cleanup_on_close", bool(on)))
        self.body.addWidget(self.on_close_box)
        self.rig_box = QtWidgets.QCheckBox("Rig checklist before SimHub is closed (off: a Yes/No question only; "
                                           "e.g. a THANOS rig parks by itself after its standby time)")
        self.rig_box.toggled.connect(lambda on: self.ctx.window._set("simhub_rig_check", bool(on)))
        self.body.addWidget(self.rig_box)
        self.b_clean = self.button("Clean up Wine…", self.clean)

    def clean(self):
        if not self.control.wine_supported():
            QtWidgets.QMessageBox.information(self, "Clean up Wine", "This needs the updated sc_start.py.")
            return
        parts = ask_parts(self, self.control.wine_scan(), self.control.settings)
        if parts:
            self.control.run_task("wine", self.control.wine_cleanup, parts)

    def refresh(self, snap):
        w = snap.get("wine")
        if w:
            self.light.set("green" if w["ok"] else "amber")
            self.status.setText("Last clean-up %s: %s" % (time.strftime("%H:%M", time.localtime(w["time"])), w["text"]))
        else:
            self.light.set("off")
            self.status.setText("No clean-up in this session")
        for box, key, default in ((self.on_close_box, "wine_cleanup_on_close", False), (self.rig_box, "simhub_rig_check", True)):
            want = bool(self.control.settings.get(key, default))
            if box.isChecked() != want:
                box.blockSignals(True)
                box.setChecked(want)
                box.blockSignals(False)
        self.b_clean.setEnabled(not snap.get("task"))
        self.b_clean.setToolTip("" if self.control.wine_supported() else "needs the updated sc_start.py")
