"""ui_base.py - shared widgets of the control window (PySide6): status light, panel card, dialogs.

A panel is one card in the window. To add one: write gui/panels/p_<id>.py with
    PANEL = {"id": "<id>", "title": "Title"}
    class Panel(ui_base.Panel):
        def build(self): ...            # add widgets / buttons to self.body and self.buttons
        def refresh(self, snap): ...    # update from control.snapshot() (twice a second)
and list it in gui/panels.json (public) or in a profile's PANELS (developer mode). Nothing else
needs to change; a panel whose file is missing is skipped.
"""
from PySide6 import QtCore, QtGui, QtWidgets

COLORS = {"off": "#6b7280", "green": "#22c55e", "blue": "#3b82f6", "amber": "#f59e0b", "red": "#ef4444"}
BRIGHT = {"off": "#9ca3af", "green": "#86efac", "blue": "#93c5fd", "amber": "#fcd34d", "red": "#fca5a5"}


class Light(QtWidgets.QWidget):
    """A round status light; pulse() flashes it (the bridge heartbeat). Painted directly: a style
    sheet change on every flash makes Qt re-polish the widget, twice a second per light."""

    def __init__(self, size=16, parent=None):
        super().__init__(parent)
        self.size_ = size
        self.color = "off"
        self._bright = False
        self.setFixedSize(size, size)
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._unflash)

    def _unflash(self):
        self._bright = False
        self.update()

    def paintEvent(self, _ev):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0, 90), 1))
        p.setBrush(QtGui.QColor((BRIGHT if self._bright else COLORS)[self.color]))
        p.drawEllipse(QtCore.QRectF(0.5, 0.5, self.size_ - 1.0, self.size_ - 1.0))
        p.end()

    def set(self, color, tip=None):
        color = color if color in COLORS else "off"
        if color != self.color:
            self.color = color
            self.update()
        if tip is not None and tip != self.toolTip():
            self.setToolTip(tip)

    def pulse(self):
        self._bright = True
        self.update()
        self._timer.start(250)


class Panel(QtWidgets.QFrame):
    """One card: [light] Title  status text / detail / buttons."""
    PANEL = {"id": "panel", "title": "Panel"}

    def __init__(self, ctx, meta, parent=None):
        super().__init__(parent)
        self.ctx = ctx                  # .control, .window, .profile, .dev
        self.control = ctx.control
        self.meta = meta
        self.setObjectName("card")
        self.setFrameShape(QtWidgets.QFrame.StyledPanel)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)
        head = QtWidgets.QHBoxLayout()
        self.light = Light()
        head.addWidget(self.light, 0, QtCore.Qt.AlignVCenter)
        self.title = QtWidgets.QLabel("<b>%s</b>" % meta.get("title", "?"))
        head.addWidget(self.title)
        if meta.get("tier") == "dev":
            tag = QtWidgets.QLabel("developer")
            tag.setStyleSheet("color:#b45309; border:1px solid #b45309; border-radius:6px; padding:0 5px; font-size:10px;")
            head.addWidget(tag)
        head.addStretch(1)
        lay.addLayout(head)
        self.status = QtWidgets.QLabel("")
        self.status.setTextFormat(QtCore.Qt.PlainText)
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        lay.addWidget(self.status)
        self.body = QtWidgets.QVBoxLayout()
        self.body.setSpacing(4)
        lay.addLayout(self.body)
        self.buttons = QtWidgets.QHBoxLayout()
        self.buttons.setSpacing(6)
        lay.addLayout(self.buttons)
        self.buttons_end = False
        self.build()
        self.buttons.addStretch(1)

    # helpers for panels
    def button(self, text, slot, tip=None):
        b = QtWidgets.QPushButton(text)
        b.clicked.connect(slot)
        if tip:
            b.setToolTip(tip)
        self.buttons.addWidget(b)
        return b

    def detail(self, small=True, rich=True):
        lab = QtWidgets.QLabel("")
        lab.setTextFormat(QtCore.Qt.RichText if rich else QtCore.Qt.PlainText)
        lab.setWordWrap(True)
        lab.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        if small:
            lab.setStyleSheet("color:palette(mid); font-size:11px;")
        self.body.addWidget(lab)
        return lab

    def build(self):
        pass

    def refresh(self, snap):
        pass

    def visible_for(self, snap):
        """False hides the card (e.g. the MFD card when the add-on is not installed)."""
        return True

    def on_close(self):
        """The window is closing. -> False to keep it open."""
        return True


# ------------------------------------------------------------------ dialogs
# Before SimHub is ended (it drives the motion rig)
RIG_OFF_ITEMS = ["Motion power (cabinet) is OFF", "Nobody is in the rig"]


def confirm_end_simhub(parent, settings, intro, ok_text="Close SimHub"):
    """Before SimHub processes are ended: the rig checklist when the setting "simhub_rig_check"
    is on (default), else one Yes/No question. (A THANOS rig parks by itself after its standby
    time, so the checklist is optional.) -> True / False."""
    if settings.get("simhub_rig_check", True):
        return confirm_checklist(parent, "Rig check", intro, RIG_OFF_ITEMS, ok_text=ok_text)
    return QtWidgets.QMessageBox.question(parent, "Close SimHub", intro + "\n\n" + ok_text + "?") == QtWidgets.QMessageBox.Yes


def confirm_checklist(parent, title, intro, items, ok_text="Confirm", warning=None):
    """A dialog where every item must be ticked before OK is enabled. -> True / False."""
    d = QtWidgets.QDialog(parent)
    d.setWindowTitle(title)
    lay = QtWidgets.QVBoxLayout(d)
    if warning:
        w = QtWidgets.QLabel(warning)
        w.setWordWrap(True)
        w.setStyleSheet("background:#7f1d1d; color:white; padding:8px; border-radius:6px; font-weight:bold;")
        lay.addWidget(w)
    lab = QtWidgets.QLabel(intro)
    lab.setWordWrap(True)
    lay.addWidget(lab)
    boxes = []
    for it in items:
        cb = QtWidgets.QCheckBox(it)
        lay.addWidget(cb)
        boxes.append(cb)
    bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Cancel)
    ok = bb.addButton(ok_text, QtWidgets.QDialogButtonBox.AcceptRole)
    ok.setEnabled(not boxes)
    for cb in boxes:
        cb.toggled.connect(lambda _=None: ok.setEnabled(all(b.isChecked() for b in boxes)))
    bb.accepted.connect(d.accept)
    bb.rejected.connect(d.reject)
    lay.addWidget(bb)
    d.setMinimumWidth(460)
    return d.exec() == QtWidgets.QDialog.Accepted


def type_yes(parent, title, text):
    """-> True when the user typed YES."""
    val, ok = QtWidgets.QInputDialog.getText(parent, title, text + "\n\nType YES to confirm:")
    return bool(ok and val.strip().upper() == "YES")


def show_text(parent, title, text, mono=True):
    d = QtWidgets.QDialog(parent)
    d.setWindowTitle(title)
    lay = QtWidgets.QVBoxLayout(d)
    t = QtWidgets.QPlainTextEdit(text)
    t.setReadOnly(True)
    if mono:
        t.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
    lay.addWidget(t)
    bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
    bb.rejected.connect(d.reject)
    bb.accepted.connect(d.accept)
    lay.addWidget(bb)
    d.resize(760, 520)
    d.exec()
