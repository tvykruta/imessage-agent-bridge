#!/bin/bash
# SMSBridge installer for macOS. Safe to run again (keeps your topic, key and settings).
#   bash install.sh [--webhook-url https://...] [--allow "+15125550100,+15125550101"]
# The webhook key is always asked for at a hidden prompt, never put on the command line.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
B="$HOME/.smsbridge"; APP="$HOME/Applications/SMSBridge.app"
LABEL="com.example.smsbridge"; PL="$HOME/Library/LaunchAgents/$LABEL.plist"
say() { printf '%s\n' "$*"; }
die() { printf '\nSetup stopped: %s\n' "$*" >&2; exit 1; }
[ "$(uname -s)" = "Darwin" ] || die "this installer is for macOS."

# 1. Find a working Python 3.8+ without triggering Apple's "install developer tools" pop-up.
#    /usr/bin/python3 is only a stub until the Command Line Tools are installed.
ok_py() { [ -x "$1" ] && "$1" -c 'import sys, sqlite3, venv; sys.exit(sys.version_info < (3, 8))' 2>/dev/null; }
SYS_PY=""
for c in /Library/Frameworks/Python.framework/Versions/Current/bin/python3 \
         /Library/Frameworks/Python.framework/Versions/3.*/bin/python3 \
         /opt/homebrew/bin/python3 /usr/local/bin/python3; do
  if ok_py "$c"; then SYS_PY="$c"; break; fi
done
if [ -z "$SYS_PY" ] && /usr/bin/xcode-select -p >/dev/null 2>&1 && ok_py /usr/bin/python3; then SYS_PY=/usr/bin/python3; fi
if [ -z "$SYS_PY" ]; then
  cat >&2 <<'T'

This Mac does not have Python 3 yet. Pick ONE, then run this installer again:
  A) Apple's Command Line Tools (about 1 GB, NOT the full Xcode app):
       xcode-select --install
     then click "Install" in the window that opens.
  B) The official installer from https://www.python.org/downloads/macos/
     (download, open the .pkg, click through).
T
  exit 1
fi
say "Using Python: $SYS_PY"

mkdir -p "$B" "$HOME/Applications" "$HOME/Library/LaunchAgents"; chmod 700 "$B"
cp "$SRC/smsbridge.py" "$B/smsbridge.py"; chmod 700 "$B/smsbridge.py"

# 2. Private Python environment. certifi gives Python trusted HTTPS certificates
#    (python.org Python has none until "Install Certificates.command" is run).
PY="$B/venv/bin/python3"
if ! ok_py "$PY"; then rm -rf "$B/venv"; "$SYS_PY" -m venv "$B/venv" || die "could not create $B/venv"; fi
"$PY" -m pip install -q --disable-pip-version-check --upgrade pynacl certifi \
  || die "could not download PyNaCl/certifi. Check the internet connection and run install.sh again."
printf '#!/bin/bash\nexec "%s" "%s" "$@"\n' "$PY" "$B/smsbridge.py" > "$B/smsbridge"; chmod 700 "$B/smsbridge"

# 3. Create or upgrade config.json. A topic + key are generated only if none exist yet.
NEW=$("$PY" - "$B/config.json" <<'PYEOF'
import base64, json, os, secrets, sys
p = sys.argv[1]
try: c = json.load(open(p))
except (OSError, ValueError): c = {"enabled": True, "webhook_url": "", "webhook_auth": "", "allowlist": [],
    "allow_contacts": False, "allow_all": False, "max_sends_per_hour": 30, "max_body_len": 1000, "poll_seconds": 15}
new = ""
if not c.get("ntfy_topic") and not c.get("outbox_url"):
    c.update(outbox_transport="ntfy", ntfy_server="https://ntfy.sh", ntfy_topic="imab-" + secrets.token_urlsafe(30),
             outbox_key=base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
    new = "NTFY_TOPIC=%s\nOUTBOX_KEY=%s" % (c["ntfy_topic"], c["outbox_key"])
fd = os.open(p + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f: json.dump(c, f, indent=2)
os.replace(p + ".tmp", p); print(new)
PYEOF
)
chmod 600 "$B/config.json"

# 4. Ask for anything still missing (webhook URL, hidden key, allowlist) and test the webhook.
#    Prompts come from Python reading the terminal, so they work the same from zsh or bash.
if ! "$PY" "$B/smsbridge.py" check >/dev/null 2>&1 || [ $# -gt 0 ]; then
  [ -r /dev/tty ] || die "run this in Terminal so it can ask for your settings."
  say ""; say "A few questions (your agent should have given you the answers):"
  "$PY" "$B/smsbridge.py" setup ${1+"$@"} < /dev/tty || die "settings were not saved."
fi
"$PY" "$B/smsbridge.py" check || die "settings are incomplete (see above). Run: ~/.smsbridge/smsbridge setup"

# 5. Tiny app wrapper so Full Disk Access is granted to SMSBridge.app only, then start at login.
rm -rf "$APP"
/usr/bin/osacompile -o "$APP" -e "set b to POSIX path of (path to home folder) & \".smsbridge/\"
do shell script quoted form of (b & \"venv/bin/python3\") & \" \" & quoted form of (b & \"smsbridge.py\")"
/usr/bin/defaults write "$APP/Contents/Info" LSUIElement -bool true
/usr/bin/defaults write "$APP/Contents/Info" CFBundleIdentifier "$LABEL"
/usr/bin/codesign --force --deep -s - "$APP" 2>/dev/null || true
cat > "$PL" <<P
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>$LABEL</string>
<key>ProgramArguments</key><array><string>$APP/Contents/MacOS/applet</string></array>
<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
<key>ThrottleInterval</key><integer>30</integer>
<key>StandardErrorPath</key><string>$B/stderr.log</string>
</dict></plist>
P
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PL"

# 6. Full Disk Access: open the right Settings page, wait, restart, then check everything.
say ""
say "Last step: allow SMSBridge to read your messages."
say "  System Settings is opening to Full Disk Access. Click +, press Cmd+Shift+G,"
say "  type ~/Applications, choose SMSBridge.app, and make sure its switch is ON."
open "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles" || true
if [ -r /dev/tty ]; then
  printf "Press Return here when that's done... "; read -r _ < /dev/tty || true
fi
launchctl kickstart -k "gui/$(id -u)/$LABEL" || true
sleep 5
say ""; "$PY" "$B/smsbridge.py" doctor || true

if [ -n "$NEW" ]; then
  say ""
  say "=== Copy these two lines and paste them to your agent (keep them private) ==="
  say "$NEW"
  say "=============================================================================="
fi
cat <<T

Done. The first time your agent sends a text, macOS asks "SMSBridge wants to
control Messages". Click OK once.
  Check everything:  ~/.smsbridge/smsbridge doctor
  Change settings:   ~/.smsbridge/smsbridge setup
  Pause:             touch ~/.smsbridge/DISABLED     (resume: rm ~/.smsbridge/DISABLED)
T
