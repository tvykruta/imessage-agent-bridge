# imessage-agent-bridge

**Let your AI agent text people from your Mac, safely.** Incoming texts go to your agent. Your agent's replies go out through Messages, but only to numbers you approve.

> ⚠️ **Free, as-is, use at your own risk.** No warranty. You're responsible for every text your agent sends. **Do your own security audit before running it.** The code is short, so read `smsbridge.py` and `outbox/app.py` or have someone you trust review them.

## Get started (the easy way)

Your AI agent does most of the work. You need a Mac signed in to Messages, and an agent that can browse the web and run a webhook, like Grok Bot.

1. **Paste [this setup prompt](AGENT_SETUP.md) into your agent** and tell it which numbers it's allowed to text. It sets up the outbox, makes the passwords, and writes your settings.
2. **Paste the one Terminal command it gives you.** Open **Terminal** (press Cmd+Space and type "Terminal"), paste, and press Return.
3. **Allow access.** When System Settings opens to **Full Disk Access**, click **+**, press **Cmd+Shift+G**, type `~/Applications`, pick **SMSBridge.app**, and turn it **on**.
4. **Test.** Text yourself, and your agent should see it. When your agent sends its first text, click **OK** on the Messages prompt.

That's it. **Pause anytime:** `touch ~/.smsbridge/DISABLED`. **Remove:** `bash uninstall.sh`.

<details><summary>Doing it by hand instead</summary>

1. **Outbox:** click New, then Blueprint on [Render](https://render.com), and point it at this repo. `render.yaml` creates the outbox and its storage and generates `OUTBOX_TOKEN`. Copy the service URL and token.
2. **Mac:** download the ZIP (green **Code** button), then run `bash install.sh` in Terminal.
3. **Settings:** edit `~/.smsbridge/config.json` with your webhook, your outbox URLs (`/outbox` and `/ack`), `Bearer <OUTBOX_TOKEN>`, and your allowlist.
4. **Restart:** `launchctl kickstart -k gui/$(id -u)/com.example.smsbridge`, then do steps 3 and 4 above.
</details>

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
