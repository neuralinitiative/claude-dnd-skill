#!/usr/bin/env python3
"""Pure-python autorun wait — TCC-safe replacement for autorun-wait.sh.

macOS TCC blocks shell-level file creation in ~/Documents, but python file
writes are permitted. autorun-wait.sh fails on its `echo > .autorun-session`
redirect; this script does the identical job (session-invalidation, countdown
broadcast, input-queue poll, /queue/consumed POST) using python I/O only.

Prints the queued player action(s) to stdout, or nothing on timeout (9 min).
"""
import os
import sys
import ssl
import time
import json
import secrets
import subprocess
import urllib.request

DISPLAY_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(DISPLAY_DIR, "..", "scripts"))
PUSH = os.path.join(DISPLAY_DIR, "push_stats.py")

try:
    from paths import runtime_dir, find_campaign
    RT = str(runtime_dir())
except Exception:
    RT = os.path.join(os.environ.get("DND_CAMPAIGN_ROOT", os.path.expanduser("~/.claude/dnd")), ".runtime")
os.makedirs(RT, exist_ok=True)

QFILE = os.path.join(RT, ".input_queue")
SESSION_FILE = os.path.join(RT, ".autorun-session")

# Invalidate any previous wait loop by writing a new session id (python write — TCC-ok)
my_session = secrets.token_hex(8)
with open(SESSION_FILE, "w", encoding="utf-8") as f:
    f.write(my_session)

# Resolve autorun interval from the active campaign's state.md (default 60s)
interval = 60
try:
    import re
    camp = open(os.path.join(RT, ".campaign"), encoding="utf-8").read().strip()
    txt = (find_campaign(camp) / "state.md").read_text(encoding="utf-8", errors="replace")
    m = re.search(r"autorun_interval:\s*(\d+)", txt)
    if m:
        interval = int(m.group(1))
except Exception:
    pass

subprocess.run([sys.executable, PUSH, "--autorun-waiting", "true", "--autorun-cycle", str(interval)],
               capture_output=True)

# Poll loop — exit when queue appears, session changes, or 9 minutes pass
content = ""
for _ in range(1800):  # 0.3s * 1800 = 9 min
    if os.path.exists(QFILE):
        try:
            content = open(QFILE, encoding="utf-8").read()
            os.unlink(QFILE)
        except Exception:
            content = ""
        break
    try:
        if open(SESSION_FILE, encoding="utf-8").read().strip() != my_session:
            break
    except Exception:
        break
    time.sleep(0.3)

# Clean up our session file if it's still ours
try:
    if open(SESSION_FILE, encoding="utf-8").read().strip() == my_session:
        os.unlink(SESSION_FILE)
except Exception:
    pass

subprocess.run([sys.executable, PUSH, "--autorun-waiting", "false"], capture_output=True)

# Clear the display queue indicator on success
if content:
    try:
        scheme_file = os.path.join(DISPLAY_DIR, ".scheme")
        scheme = open(scheme_file, encoding="utf-8").read().strip() if os.path.exists(scheme_file) else "http"
        token = open(os.path.join(RT, ".token"), encoding="utf-8").read().strip()
        ctx = None
        if scheme == "https":
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(f"{scheme}://localhost:5001/queue/consumed",
                                     data=b"", method="POST",
                                     headers={"X-DND-Token": token})
        urllib.request.urlopen(req, timeout=1, context=ctx)
    except Exception:
        pass

sys.stdout.write(content)
