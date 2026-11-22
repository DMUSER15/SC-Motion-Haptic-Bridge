"""Motion bridge card: heartbeat, certificates, start / stop / repair."""
from html import escape as esc

from PySide6 import QtCore, QtWidgets

import ui_base
from ui_base import Panel as Base

PANEL = {"id": "bridge", "title": "Motion bridge"}
STEP_ICON = {"ok": "✔", "warn": "⚠", "fail": "✖", "run": "…", "decide": "?"}


class Panel(Base):
    def build(self):
        row = QtWidgets.QHBoxLayout()
        self.hb_light = ui_base.Light(12)
        row.addWidget(self.hb_light)
        self.hb_text = QtWidgets.QLabel("heartbeat")
        self.hb_text.setTextFormat(QtCore.Qt.PlainText)       # log text is never markup
        self.hb_text.setWordWrap(True)
        row.addWidget(self.hb_text, 1)
        self.body.addLayout(row)
        crow = QtWidgets.QHBoxLayout()
        self.cert_light = ui_base.Light(12)
        crow.addWidget(self.cert_light)
        self.cert_text = QtWidgets.QLabel("certificates: not checked yet")
        self.cert_text.setTextFormat(QtCore.Qt.PlainText)
        self.cert_text.setWordWrap(True)
        crow.addWidget(self.cert_text, 1)
        self.body.addLayout(crow)
        self.steps = self.detail()
        self.decision = QtWidgets.QLabel("")
        self.decision.setTextFormat(QtCore.Qt.PlainText)
        self.decision.setWordWrap(True)
        self.decision.setStyleSheet("background:#78350f; color:white; padding:6px; border-radius:6px;")
        self.decision.hide()
        self.body.addWidget(self.decision)
        self.b_start = self.button("Start motion", self.start)
        self.b_stop = self.button("Stop motion", self.stop)
        self.b_repair = self.button("Repair…", self.repair,
                                    "Puts your working LiveMotion certificate / connector copies back (backups first), "
                                    "checks them, and starts the bridge again. The game must be closed.")
        self.b_cont = self.button("Continue anyway", self.cont)
        self.b_cont.hide()
        self.last_seq = None

    def start(self):
        snap = self.control.snapshot()
        if snap["pipeline"] == "running":
            self.control.run_task("start", self.control.restart_pipeline)
        else:
            self.control.run_task("start", self.control.start_pipeline)

    def stop(self):
        snap = self.control.snapshot()
        if snap["game"]["game"]:
            if QtWidgets.QMessageBox.question(self, "Stop motion", "Star Citizen is running. Stopping the bridge ends "
                                              "motion and shaker effects until you start it again. Stop now?") != QtWidgets.QMessageBox.Yes:
                return
        self.control.run_task("stop", self.control.stop_pipeline)

    def repair(self):
        snap = self.control.snapshot()
        if snap["game"]["game"]:
            QtWidgets.QMessageBox.information(self, "Repair", "Close Star Citizen first: the certificate copies cannot be "
                                              "repaired while the game runs.")
            return
        if QtWidgets.QMessageBox.question(self, "Repair the motion bridge",
                                          "This stops the bridge and capture server, puts your working certificate and "
                                          "connector copies back from your saved copies (backups of the current ones "
                                          "first), checks them, and starts the bridge again. SimHub keeps running.\n\n"
                                          "Repair now?") != QtWidgets.QMessageBox.Yes:
            return
        self.control.run_task("repair", self.control.repair)

    def cont(self):
        self.control.run_task("start", self.control.restart_pipeline, override_cert=True)

    def refresh(self, snap):
        b = snap["bridge"]
        hb = b.get("hb") or {}
        self.light.set({"running": b["light"] if b["light"] != "off" else "amber", "starting": "amber",
                        "needs_decision": "amber", "failed": "red"}.get(snap["pipeline"], "off" if not b["alive"] else b["light"]))
        state = {"idle": "not started", "starting": "starting…", "running": "running", "needs_decision": "waiting for your choice",
                 "failed": "STOPPED: " + (snap["pipeline_detail"] or "failed"), "stopped": "stopped"}.get(snap["pipeline"], snap["pipeline"])
        extra = ""
        if snap["addons"]:
            extra = "; add-ons: " + ", ".join("%s %s" % (n, "running" if a else "STOPPED") for n, a in snap["addons"])
        self.status.setText("Pipeline %s%s" % (state, extra))
        self.hb_light.set(b["light"], b["text"])
        if hb.get("seq") is not None and hb.get("seq") != self.last_seq:
            self.last_seq = hb["seq"]
            self.hb_light.pulse()
        self.hb_text.setText("Heartbeat: " + b["text"])
        c = snap["cert"]
        self.cert_light.set(c["light"])
        st = c.get("selftest")
        self.cert_text.setText("LiveMotion certificates: %s%s%s" % (
            c["text"], "" if st is None else ("; TLS self-test %s" % ("passed" if st == 0 else "FAILED (exit %s)" % st)),
            " (you chose to continue)" if c.get("override") else ""))
        self.steps.setText("<br>".join("%s %s: %s" % (STEP_ICON.get(s, "·"), esc(n), esc(t)) for n, s, t in snap["steps"][-9:]))
        dec = snap.get("decision")
        if dec and snap["pipeline"] == "needs_decision":
            self.decision.setText(dec["text"])
            self.decision.show()
            self.b_cont.show()
        else:
            self.decision.hide()
            self.b_cont.hide()
        busy = bool(snap.get("task"))
        running = snap["pipeline"] == "running"
        self.b_start.setText("Restart motion" if running else "Start motion")
        self.b_start.setEnabled(not busy)
        self.b_stop.setEnabled(not busy and (running or b["alive"]))
        self.b_repair.setEnabled(not busy and not snap["game"]["game"])
        self.b_cont.setEnabled(not busy)
        rep = snap.get("repair")
        if rep and rep.get("text") and not busy:
            self.b_repair.setToolTip("last repair: %s" % ("done" if rep.get("ok") else "did not fix it (sc_lmcert exit %s)" % rep.get("rc")))
