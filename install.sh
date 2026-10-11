#!/bin/bash
# SMSBridge installer. Run once: bash install.sh
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
B="$HOME/.smsbridge"; APP="$HOME/Applications/SMSBridge.app"
PL="$HOME/Library/LaunchAgents/com.example.smsbridge.plist"
[ -x /usr/bin/python3 ] && /usr/bin/python3 -c 'import sqlite3' 2>/dev/null || { echo "python3 missing: run 'xcode-select --install' then rerun."; exit 1; }
mkdir -p "$B" "$HOME/Applications" "$HOME/Library/LaunchAgents"; chmod 700 "$B"
cp "$SRC/smsbridge.py" "$B/smsbridge.py"; chmod 700 "$B/smsbridge.py"
if [ ! -f "$B/config.json" ]; then
cat > "$B/config.json" <<'J'
{
  "enabled": true,
  "webhook_url": "https://your-agent.example.com/webhook",
  "webhook_auth": "Bearer YOUR_WEBHOOK_KEY",
  "outbox_url": "https://your-outbox.example.com/outbox",
  "ack_url": "https://your-outbox.example.com/ack",
  "outbox_auth": "Bearer YOUR_OUTBOX_TOKEN",
  "allowlist": ["+15555550100"],
  "allow_contacts": false,
  "max_sends_per_hour": 30,
  "max_body_len": 1000,
  "poll_seconds": 15
}
J
fi
chmod 600 "$B/config.json"
# Tiny app wrapper so Full Disk Access is granted to SMSBridge.app only.
rm -rf "$APP"
/usr/bin/osacompile -o "$APP" -e "do shell script \"/usr/bin/python3 \" & quoted form of (POSIX path of (path to home folder) & \".smsbridge/smsbridge.py\")"
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
cat <<T
Installed. Next:
 1. In Full Disk Access, click +, press Cmd+Shift+G, type ~/Applications, pick SMSBridge.app, turn it ON.
 2. Edit $B/config.json (webhook + outbox URLs and keys).
 3. Restart: launchctl kickstart -k gui/$(id -u)/com.example.smsbridge
 4. First send: macOS asks "SMSBridge wants to control Messages" -> click OK (one time).
 Pause anytime: touch ~/.smsbridge/DISABLED     Log: ~/.smsbridge/log.jsonl
T
