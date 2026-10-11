# imessage-agent-bridge

**Let your AI agent text people from your Mac, safely.** Incoming texts go to your agent. Your agent's replies go out through Messages, but only to numbers you approve.

> ⚠️ **Free, as-is, use at your own risk.** No warranty. You're responsible for every text your agent sends. **Do your own security audit before running it.** The code is short, so read `smsbridge.py` and `outbox/app.py` or have someone you trust review them.

## Get started (non-technical)

You need: a Mac signed in to Messages, an AI agent that can receive a webhook and make web requests, and about 20 minutes.

1. **Download** this repo: click the green **Code** button, then **Download ZIP**, and unzip it.
2. **Set up the outbox.** This is a small online mailbox your agent drops texts into. Deploy the `outbox` folder to any Python host with Redis (Render, Railway, Fly). Set a long random password as `OUTBOX_TOKEN`.
3. **Install on your Mac.** Open **Terminal**, type `bash `, drag `install.sh` into the window, and press Return.
4. **Give it permission.** System Settings opens to **Full Disk Access**. Click **+**, press **Cmd+Shift+G**, type `~/Applications`, pick **SMSBridge.app**, and switch it **on**.
5. **Fill in your settings.** In Finder, press **Cmd+Shift+G**, go to `~/.smsbridge/`, and open `config.json` in TextEdit. Fill in:
   - your agent's webhook address and key
   - your outbox address and `OUTBOX_TOKEN`
   - `allowlist`: the only numbers it may text, like `+15125550100`
6. **Restart it.** Paste this into Terminal: `launchctl kickstart -k gui/$(id -u)/com.example.smsbridge`
7. **Test it.** Text yourself and check that your agent receives it. Have your agent send one text, and click **OK** when macOS asks to control Messages.

**Stop it any time:** `touch ~/.smsbridge/DISABLED`
**Remove it:** `bash uninstall.sh`

## How it works

```
Texts in:   Messages → your Mac (read-only) → your agent's webhook
Texts out:  your agent → outbox → your Mac checks the rules → Messages
```

## Why it's built to be safe

- **No shell for the agent.** It can only *ask* to send a text. Your Mac decides.
- **Allowlist only.** Numbers not on your list are rejected.
- **Hard limits on the Mac:** 30 texts an hour, 1,000 characters, each text sent once.
- **Read-only history.** It never edits or replays old messages.
- **About 200 lines of plain Python with no extra packages.** Easy to read in one sitting.
- **Security reviewed.** Every finding was fixed. See [SECURITY.md](SECURITY.md).

## Why you should still be careful

- **Agents get tricked.** A malicious text can push your agent to send something bad to an approved number. Keep your allowlist short.
- **Your agent sees every text you get,** including 2FA codes. Only connect an agent you trust.
- **Your outbox token is a password.** Anyone who has it can queue texts.
- **Texts go out as you,** from your number and Apple ID.
- **macOS updates can break it.** This isn't an Apple feature.

## Works with

Tested with **Grok Bot**. It should work with anything that can receive a webhook and send an authenticated POST, but these haven't been tested: Claude Code/Desktop, ChatGPT agents/Codex, OpenClaw, n8n/Zapier/Make, Home Assistant, and LangChain/LangGraph.

- **Inbound:** set `webhook_url` to your agent's webhook.
- **Outbound:** your agent sends `POST /enqueue {"id","to","body"}` with `Authorization: Bearer $OUTBOX_TOKEN`.

## Troubleshooting

- **`authorization denied` on `chat.db`:** SMSBridge.app doesn't have Full Disk Access. Turn it on, then restart (step 6).
- **Nothing sends:** check that `outbox_url` is set, and that `outbox_auth` is `Bearer <OUTBOX_TOKEN>`, not your webhook key.
- **Logs** are in `~/.smsbridge/log.jsonl`, and crashes go to `stderr.log`. The log contains message text, so keep it private.

## For developers

- Outbox environment variables: `REDIS_URL`, `OUTBOX_TOKEN` (at least 24 characters), `PORT`, and `PENDING_TTL`.
- Outbox endpoints: `/healthz`, `/outbox`, `/enqueue`, `/ack` and `/status`.
- All settings are in `config.example.json`.
- Run the tests with `python3 -m unittest discover tests`.

MIT License · [Security policy](SECURITY.md)
