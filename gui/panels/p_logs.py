"""Logs card: open the newest log in your text editor, or pick one in the log viewer."""
import os
import time

from PySide6 import QtGui, QtWidgets

import scm_control as C
from ui_base import Panel as Base

PANEL = {"id": "logs", "title": "Logs"}


class LogViewer(QtWidgets.QDialog):
    def __init__(self, control, parent=None):
        super().__init__(parent)
        self.control = control
        self.setWindowTitle("Log viewer")
        lay = QtWidgets.QHBoxLayout(self)
        left = QtWidgets.QVBoxLayout()
        self.list = QtWidgets.QListWidget()
        self.list.currentRowChanged.connect(self.show_one)
        left.addWidget(self.list, 1)
        row = QtWidgets.QHBoxLayout()
        b = QtWidgets.QPushButton("Open in editor")
        b.clicked.connect(self.open_one)
        row.addWidget(b)
        b2 = QtWidgets.QPushButton("Open folder")
        b2.clicked.connect(self.open_folder)
        row.addWidget(b2)
        b3 = QtWidgets.QPushButton("Refresh")
        b3.clicked.connect(self.fill)
        row.addWidget(b3)
        left.addLayout(row)
        lay.addLayout(left, 2)
        self.text = QtWidgets.QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setFont(QtWidgets.QApplication.font())
        lay.addWidget(self.text, 5)
        self.items = []
        self.fill()
        self.resize(1100, 600)

    def fill(self):
        self.list.clear()
        self.items = sorted(self.control.log_sources(), key=lambda lp: -C.mtime(lp[1]))
        for label, path in self.items:
            self.list.addItem("%s  %s  (%s)" % (time.strftime("%m-%d %H:%M", time.localtime(C.mtime(path))),
                                              os.path.basename(path), label))
        if self.items:
            self.list.setCurrentRow(0)

    def show_one(self, i):
        if 0 <= i < len(self.items):
            path = self.items[i][1]
            try:
                with open(path, "rb") as f:
                    f.seek(0, 2)
                    size = f.tell()
                    f.seek(max(0, size - 200_000))
                    data = f.read().decode("utf-8", "replace")
            except OSError as e:
                data = "cannot read %s (%s)" % (path, e)
            self.text.setPlainText(data)
            self.text.moveCursor(QtGui.QTextCursor.MoveOperation.End)

    def open_one(self):
        i = self.list.currentRow()
        if 0 <= i < len(self.items):
            self.control.open_path(self.items[i][1])

    def open_folder(self):
        i = self.list.currentRow()
        if 0 <= i < len(self.items):
            self.control.open_path(os.path.dirname(self.items[i][1]))


class Panel(Base):
    def build(self):
        self.b_latest = self.button("Open latest log", self.open_latest, "Opens the newest log in your default text editor")
        self.b_view = self.button("Log viewer…", lambda: LogViewer(self.control, self).exec())
        self.t_last = 0.0
        self.latest = None
        self.new_errors = []

    def open_latest(self):
        path = self.control.latest_log()
        if path:
            self.control.open_path(path)

    def refresh(self, snap):
        now = time.time()
        if now - self.t_last > 5:
            self.t_last = now
            srcs = self.control.log_sources()
            self.latest = max(srcs, key=lambda lp: C.mtime(lp[1]))[1] if srcs else None
            started = getattr(self.ctx, "started", now)
            self.new_errors = [p for lab, p in srcs if lab == "error log" and C.mtime(p) >= started]
        if self.new_errors:
            self.light.set("amber", "a problem was logged since this window opened")
        else:
            self.light.set("off")
        if self.latest:
            self.status.setText("%snewest: %s (%s)" % ("%d new error log(s); " % len(self.new_errors) if self.new_errors else "",
                                                    os.path.basename(self.latest),
                                                    time.strftime("%H:%M:%S", time.localtime(C.mtime(self.latest)))
                                                    if os.path.exists(self.latest) else "?"))
        else:
            self.status.setText("no logs yet")
        self.b_latest.setEnabled(bool(self.latest))
