#!/bin/bash
launchctl bootout "gui/$(id -u)/com.example.smsbridge" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/com.example.smsbridge.plist"
rm -rf "$HOME/Applications/SMSBridge.app"
echo "Removed app + LaunchAgent. Config/logs kept in ~/.smsbridge (rm -rf it to delete)."
echo "Also remove SMSBridge from System Settings > Privacy & Security > Full Disk Access."
