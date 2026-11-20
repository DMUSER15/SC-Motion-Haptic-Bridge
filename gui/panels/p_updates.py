"""Updates card: what the update check found for each installed app."""
from html import escape as esc

from ui_base import Panel as Base

PANEL = {"id": "updates", "title": "Updates"}


class Panel(Base):
    def build(self):
        self.info = self.detail()
        self.b_check = self.button("Check now", lambda: self.ctx.window.check_updates(prompt=True))
        self.b_show = self.button("Show updates…", self.ctx.window.show_update_dialog)

    def refresh(self, snap):
        recs = snap.get("updates")
        busy = snap.get("task")
        self.b_check.setEnabled(not busy)
        if recs is None:
            self.light.set("off")
            self.status.setText("checking…" if busy == "checking for updates" else "not checked yet")
            self.b_show.setEnabled(False)
            return
        ups = [r for r in recs if r.get("update")]
        probs = [r for r in recs if r.get("problem") and not r.get("update")]
        if ups:
            self.light.set("red" if any(r.get("motion") for r in ups) else "blue")
            self.status.setText("%d update(s) available: %s" % (len(ups), ", ".join(
                "%s %s → %s" % (r["id"], r["current"], r["available"]) for r in ups)))
        elif probs:
            self.light.set("amber")
            self.status.setText("could not check everything")
        else:
            self.light.set("green")
            self.status.setText("everything is up to date")
        self.b_show.setEnabled(bool(ups))
        self.info.setText("<br>".join(esc("%s: %s%s" % (r["title"], r.get("current") or "?",
                                                      (" — " + r["problem"]) if r.get("problem") else "")) for r in recs))
