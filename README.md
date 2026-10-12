# imessage-agent-bridge

**Let your AI agent text people from your Mac, safely.** Incoming texts go to your agent. Your agent's replies go out through Messages, but only to numbers you approve.

<p align="center"><img src="docs/demo.gif" alt="Demo: a text arrives, the agent drafts a reply, the Mac bridge checks the rules, and Messages sends it" width="720"></p>

> ⚠️ **Free, as-is, use at your own risk.** No warranty. You're responsible for every text your agent sends. **Do your own security audit before running it.** The code is short, so read `smsbridge.py` and `install.sh` or have someone you trust review them.

## Get started

You need a Mac signed in to Messages and an AI agent that can run a little Python, like Grok Bot. **No accounts or servers to sign up for.** If the Mac has no Python yet, the installer tells you how to get it (Apple's Command Line Tools or python.org; you don't need the full Xcode app).

1. **Tell your agent:**
   > Set up https://github.com/tvykruta/imessage-agent-bridge for me. Follow AGENT_SETUP.md. Only let it text these numbers: …
2. **Paste the one Terminal command it gives you.** The installer asks for the webhook key (typing is hidden), sends your agent a test message, and opens Full Disk Access. Add SMSBridge.app there and press Return.
3. **Paste the two lines it prints** (`NTFY_TOPIC=` and `OUTBOX_KEY=`) back to your agent. The first time a text goes out, click OK when macOS asks to control Messages.

Something not working? Run `~/.smsbridge/smsbridge doctor`. It checks everything and tells you what to fix.

**Pause anytime:** `touch ~/.smsbridge/DISABLED`. **Remove:** `bash uninstall.sh`.

<details><summary>Doing it by hand instead</summary>

1. **Mac:** download the ZIP (green **Code** button), then run `bash install.sh` in Terminal. It asks for your agent's webhook URL, its key and the numbers to allow, tests the webhook, and walks you through Full Disk Access. It prints a topic and key at the end.
2. **Agent:** give it the topic and key, and have it send with `send.py`.
3. **Later changes:** `~/.smsbridge/smsbridge setup`. Health check: `~/.smsbridge/smsbridge doctor`.
</details>

## How it works

```
Texts in:   Messages → your Mac (read-only) → your agent's webhook
Texts out:  your agent → encrypted note on ntfy.sh → your Mac decrypts, checks the rules → Messages
```

Your agent never connects to your Mac, and nothing connects in. The Mac only makes outgoing requests.

## The relay is not trusted

Outgoing texts pass through [ntfy.sh](https://ntfy.sh), a free public notification relay. Anyone who guesses or learns the topic name can see that encrypted notes are posted there, but:

- **They can't read them.** Each note is encrypted with a 32-byte key that only your Mac and your agent have (PyNaCl SecretBox: XSalsa20-Poly1305).
- **They can't fake or alter them.** Anything that fails to decrypt is dropped.
- **They can't replay them.** Notes older than 10 minutes, or reusing an id, are dropped.
- **The Mac still decides.** Allowlist, rate limit, length cap and kill switch apply to every decrypted text.

The topic is random (`imab-` plus 40 characters), so it isn't guessable either.

**Honest limits of the free public ntfy.sh:** notes are kept for about 12 hours, so a text queued while your Mac is off longer than that is lost. Messages are capped at 4096 bytes (about 1,000 characters of text after encryption). The free tier allows roughly 250 messages a day per IP address. It's a best-effort service with no uptime promise. If that's not enough, point `ntfy_server` at a [self-hosted ntfy](https://docs.ntfy.sh/install/), or host your own queue (below).

## Under the hood

**Inbound (Mac → agent)**
1. Opens `~/Library/Messages/chat.db` **read-only** (`file:...?mode=ro`).
2. Tracks the last seen `message.ROWID` in `~/.smsbridge/state.json`. On first run it starts at `MAX(ROWID)`, so history is never replayed.
3. Every `poll_seconds` (default 15) it selects `ROWID > last`, decodes `text` (or the `attributedBody` NSAttributedString typedstream when `text` is NULL), and appends to a local on-disk queue. It reads at most 200 new messages per poll.
4. Flushes the queue in order with `POST webhook_url` (Bearer auth, plus an `Idempotency-Key: imab-<rowid>` header so your receiver can drop duplicates after retries). On failure, it stops and keeps the rest on disk, retrying with exponential backoff (15s → 300s max).

**Outbound (agent → Mac), default ntfy mode**
1. The agent encrypts `{id, to, body, ts}` and posts it to `https://ntfy.sh/<topic>` (`send.py` does this in a few lines).
2. Every `poll_seconds` the Mac calls `GET <topic>/json?poll=1&since=<last id>` and remembers the last ntfy message id.
3. Each note is decrypted and checked for age and replay (ids remembered for 24 hours). At most 20 bad notes are logged per poll.
4. Valid texts go through the guardrails, then a fixed AppleScript template sends them through Messages.app (`osascript` with recipient/body passed as argv, never `shell=True`).
5. Optional: with `"ntfy_acks": true`, encrypted receipts `{id, status: sent|failed|rejected}` are posted to `<topic>-acks`.

**Guardrails (enforced on the Mac, not by the agent)**
- Recipient allowlist (`allowlist`), optionally plus numbers in your Contacts (`allow_contacts`). `allow_all: true` turns the allowlist off entirely, so any number can be texted. Not recommended. The other limits still apply.
- Rate limit: `max_sends_per_hour` (default 30).
- Body cap: `max_body_len` (default 1000).
- Idempotent ids: each id is sent at most once.
- Kill switch: `touch ~/.smsbridge/DISABLED` or set `"enabled": false`.

## Why it's built to be safe

- **No shell for the agent.** It can only *ask* to send a text. Your Mac decides.
- **No open ports.** The Mac only polls out. Nothing on the internet can connect to it.
- **Allowlist only.** Numbers not on your list are rejected.
- **Hard limits on the Mac:** 30 texts an hour, 1,000 characters, each text sent once.
- **Read-only history.** It never edits or replays old messages.
- **About 550 lines of plain Python, including setup and health checks.** The only extra packages are PyNaCl and certifi (trusted HTTPS certificates), installed into a private environment in `~/.smsbridge`.
- **Security reviewed.** See [SECURITY.md](SECURITY.md).

## Why you should still be careful

- **Agents get tricked.** A malicious text can push your agent to send something bad to an approved number. Keep your allowlist short.
- **Your agent sees every text you get,** including 2FA codes. Only connect an agent you trust.
- **Your outbox key is a password.** Anyone who has it (and the topic) can queue texts to your allowlisted numbers.
- **Texts go out as you,** from your number and Apple ID.
- **macOS updates can break it.** This isn't an Apple feature.

## Works with

Tested with **Grok Bot**. It should work with any agent that can receive a webhook and run a short Python snippet (or do the same encryption in another language with libsodium), but these haven't been tested: Claude Code/Desktop, ChatGPT agents/Codex, OpenClaw, n8n/Zapier/Make, Home Assistant, and LangChain/LangGraph.

- **Inbound:** set `webhook_url` to your agent's webhook.
- **Outbound:** `NTFY_TOPIC=... OUTBOX_KEY=... python3 send.py +15555550100 "hi"`.

## Advanced: host your own queue

Prefer not to use a public relay? The original HTTP outbox is still supported.

1. Deploy `outbox/app.py` with Redis anywhere. The included `render.yaml` does it on a free [Render](https://render.com) account (New, then Blueprint, then pick this repo). It generates `OUTBOX_TOKEN`.
2. In `config.json` set `"outbox_transport": "http"`, `outbox_url` (`<host>/outbox`), `ack_url` (`<host>/ack`) and `outbox_auth` (`Bearer <OUTBOX_TOKEN>`).
3. Your agent sends `POST /enqueue {"id","to","body"}` with `Authorization: Bearer $OUTBOX_TOKEN`.

Existing configs with `outbox_url` set and no `ntfy_topic` keep using HTTP mode automatically. Outbox environment variables: `REDIS_URL`, `OUTBOX_TOKEN` (at least 24 characters), `PORT`, `PENDING_TTL`. Endpoints: `/healthz`, `/outbox`, `/enqueue`, `/ack`, `/status`.

## Troubleshooting

Start with `~/.smsbridge/smsbridge doctor`. It checks Python, certificates, the Mac's clock, your settings, the webhook (with a test message), ntfy.sh, Full Disk Access and Messages permission.

- **No Python / a prompt to install Xcode:** you only need Apple's Command Line Tools (`xcode-select --install`) or Python from python.org. Rerun the installer afterward.
- **`CERTIFICATE_VERIFY_FAILED`:** rerun the installer. It installs `certifi`, so python.org Python works without "Install Certificates.command".
- **`authorization denied` on `chat.db`:** SMSBridge.app doesn't have Full Disk Access. Turn it on, then restart.
- **`not_allowlisted`:** the number isn't on your list. Run `~/.smsbridge/smsbridge setup`.
- **Webhook `HTTP Error 401`:** wrong webhook key. Run `~/.smsbridge/smsbridge setup` to re-enter it.
- **Nothing sends:** look for `ntfy_reject` in the log. Each line has a `hint`, plus `mac_time`, `note_ts` and `relay_time`. `stale` or `future` means a clock is off: compare those three numbers to see whose. `bad_ciphertext` means the agent has the wrong key.
- **Logs** are in `~/.smsbridge/log.jsonl`, and crashes go to `stderr.log`. The log contains message text, so keep it private.

## For developers

- All settings are in `config.example.json`. `tz_offset_hours` sets a fixed UTC offset for message timestamps (for example `-5`). Leave it `null` to use the Mac's local time.
- Run the tests with `pip install pynacl certifi && python3 -m unittest discover tests`.

MIT License · [Security policy](SECURITY.md)
