#!/bin/bash
# SMSBridge installer. Run once: bash install.sh
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
B="$HOME/.smsbridge"; APP="$HOME/Applications/SMSBridge.app"
PL="$HOME/Library/LaunchAgents/com.example.smsbridge.plist"
[ -x /usr/bin/python3 ] && /usr/bin/python3 -c 'import sqlite3' 2>/dev/null || { echo "python3 missing: run 'xcode-select --install' then rerun."; exit 1; }
mkdir -p "$B" "$HOME/Applications" "$HOME/Library/LaunchAgents"; chmod 700 "$B"
cp "$SRC/smsbridge.py" "$B/smsbridge.py"; chmod 700 "$B/smsbridge.py"
# Private Python env with PyNaCl (used to decrypt texts from the ntfy relay).
PY="$B/venv/bin/python3"
[ -x "$PY" ] || /usr/bin/python3 -m venv "$B/venv"
"$PY" -m pip install -q --disable-pip-version-check --upgrade pynacl \
  || { echo "Could not install PyNaCl (needs internet). Rerun install.sh when online."; exit 1; }
# Create/upgrade config. A new ntfy topic + key are generated only if none exist yet.
NEW=$("$PY" - "$B/config.json" <<'PYEOF'
import base64, json, os, secrets, sys
p = sys.argv[1]
try: c = json.load(open(p))
except (OSError, ValueError): c = {"enabled": True, "webhook_url": "https://your-agent.example.com/webhook",
    "webhook_auth": "Bearer YOUR_WEBHOOK_KEY", "allowlist": ["+15555550100"], "allow_contacts": False,
    "max_sends_per_hour": 30, "max_body_len": 1000, "poll_seconds": 15}
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
# Tiny app wrapper so Full Disk Access is granted to SMSBridge.app only.
rm -rf "$APP"
/usr/bin/osacompile -o "$APP" -e "set b to POSIX path of (path to home folder) & \".smsbridge/\"
do shell script quoted form of (b & \"venv/bin/python3\") & \" \" & quoted form of (b & \"smsbridge.py\")"
/usr/bin/defaults write "$APP/Contents/Info" LSUIElement -bool true
/usr/bin/defaults write "$APP/Contents/Info" CFBundleIdentifier com.example.smsbridge
/usr/bin/codesign --force --deep -s - "$APP" 2>/dev/null || true
cat > "$PL" <<P
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.example.smsbridge</string>
<key>ProgramArguments</key><array><string>$APP/Contents/MacOS/applet</string></array>
<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
<key>ThrottleInterval</key><integer>30</integer>
<key>StandardErrorPath</key><string>$B/stderr.log</string>
</dict></plist>
P
launchctl bootout "gui/$(id -u)/com.example.smsbridge" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PL"
open "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"
if [ -n "$NEW" ]; then
echo "=== Give these two lines to your agent (shown once; also saved in $B/config.json) ==="
echo "$NEW"
echo "=================================================================================="
fi
cat <<T
Installed. Next:
 1. In Full Disk Access, click +, press Cmd+Shift+G, type ~/Applications, pick SMSBridge.app, turn it ON.
 2. Edit $B/config.json (webhook_url, webhook_auth, allowlist).
 3. Restart: launchctl kickstart -k gui/$(id -u)/com.example.smsbridge
 4. First send: macOS asks "SMSBridge wants to control Messages" -> click OK (one time).
 Pause anytime: touch ~/.smsbridge/DISABLED     Log: ~/.smsbridge/log.jsonl
T
