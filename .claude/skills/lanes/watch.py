"""The coordinator's lane watch (lanes skill, step 3). Run it as a Monitor:
    python3 -u watch.py <dir>
Prints each new line of <dir>/attention.log (one event per lane that needs the user), and
colours each lane's Konsole tab: red while it needs the user, green once they have answered
(a new "user" record in the lane's transcript after the point the hook recorded).
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(sys.argv[1])
LOG, STATE = ROOT / "attention.log", ROOT / "state"
COLOURS = {"attention": "#c0392b", "working": "#3b8f5a"}
SERVICE = os.environ["KONSOLE_DBUS_SERVICE"]


def colour(session, state):
    if session:
        subprocess.run(["qdbus-qt6", SERVICE, session, "setTabColor", COLOURS[state]],
                       capture_output=True)


def answered(transcript, offset):
    try:
        with open(transcript, "rb") as f:
            f.seek(offset)
            for line in f:
                try:
                    if json.loads(line).get("type") == "user":
                        return True
                except ValueError:
                    pass
    except OSError:
        pass
    return False


STATE.mkdir(parents=True, exist_ok=True)
LOG.touch()
pos = LOG.stat().st_size
shown = {}
while True:
    if LOG.stat().st_size > pos:
        with LOG.open() as f:
            f.seek(pos)
            for line in f:
                print(line.rstrip(), flush=True)
            pos = f.tell()
    for p in STATE.glob("*.json"):
        try:
            s = json.loads(p.read_text())
        except ValueError:
            continue
        if s["state"] == "attention" and answered(s["transcript"], s["offset"]):
            s["state"] = "working"
            p.write_text(json.dumps(s))
        if shown.get(p.name) != (s["session"], s["state"]):
            colour(s["session"], s["state"])
            shown[p.name] = (s["session"], s["state"])
    time.sleep(1)
