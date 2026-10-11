# imessage-agent-bridge

Give an AI agent narrow, auditable iMessage/SMS access on a Mac — without a shell, and without approving every send by hand.

Two pieces:

- `smsbridge.py` — a ~200-line, stdlib-only Python daemon run by a LaunchAgent on your Mac.
- `outbox/app.py` — a tiny authenticated HTTP queue (Redis-backed) that the agent writes outbound texts into.

> **Use at your own risk.** This is a free, personal project released as-is under the MIT License, with no warranty. You are responsible for how you set it up and for every message your agent sends. Read [Safety, and why to be careful](#safety-and-why-to-be-careful) before installing.
>
> **Do your own security audit before running it.** This code reads your private messages and sends texts as you. Don't rely on our review alone: read `smsbridge.py` and `outbox/app.py` yourself (they're short), or have someone you trust review them, and check the exact version you install.

## What it does

It lets an AI agent text through your Mac's Messages app, and lets it hear replies.

- **Incoming texts:** when someone texts you, the Mac forwards the message (sender, text, time) to your agent's webhook. The agent can then read it and decide what to do.
- **Outgoing texts:** when your agent wants to send a text, it drops it in a small queue on the internet (the "outbox"). Your Mac checks the queue, makes sure the text follows your rules, sends it from your Messages app, and reports back.

Example use: an assistant that gets replies from contractors by text and answers routine scheduling questions, without you copy-pasting between apps.

## Safety, and why to be careful

**Why it's designed to be safe**
- **The agent never touches your Mac.** It can't run commands, open files or read your other messages. It can only put texts in the outbox. Your Mac decides whether to send them.
- **You choose who can be texted.** Only numbers on your allowlist get messages. Texts to anyone else are rejected and logged.
- **Limits are enforced on your Mac, not by the agent:** a maximum number of texts per hour, a maximum message length, and each text is sent at most once.
- **History is read-only and never replayed.** The bridge reads your Messages database without changing it, and starts from new messages only.
- **Instant off switch:** `touch ~/.smsbridge/DISABLED` stops all sending.
- **Secrets stay private:** the settings file is readable only by you, and tokens are checked safely.
- **Small and readable:** about 200 lines of plain Python with no outside packages, so you can read the whole thing. It went through a security review, and every finding was fixed. See [SECURITY.md](SECURITY.md).

**Why you should still be careful**
- **Your agent can still make mistakes.** If it's tricked by a message (prompt injection) or just gets it wrong, it can send a bad text to someone on your allowlist. Keep the allowlist short.
- **Every incoming text goes to your agent.** That includes private messages and two-factor login codes. Only connect an agent and webhook you trust.
- **Full Disk Access is powerful.** The helper needs it to read Messages. Anything running as that helper can read files it shouldn't, so only run this exact code.
- **Anyone who has your outbox token can queue texts** to your allowlisted numbers. Treat it like a password.
- **Texts go out under your name** from your Apple ID and number. Carrier and Apple rules still apply.
- **This isn't a supported Apple feature.** macOS updates can break it without warning.

**Audit it yourself first**
We strongly recommend that everyone does their own security audit before running this. Read the code, check that the guardrails fit your setup, and pin the exact commit you reviewed. A past review doesn't guarantee it's safe for you.

**Disclaimer**
This software is provided free of charge, "as is", without warranty of any kind. The author isn't responsible for any messages sent, data exposed, costs, account problems, or other harm from using it. By using it, you accept full responsibility. See [LICENSE](LICENSE).

## How it works

**Inbound (Mac → agent)**
1. Opens `~/Library/Messages/chat.db` **read-only** (`file:...?mode=ro`).
2. Tracks the last seen `message.ROWID` in `~/.smsbridge/state.json`. On first run it starts at `MAX(ROWID)`, so history is never replayed.
3. Every `poll_seconds` (default 15) it selects `ROWID > last`, decodes `text` (or the `attributedBody` NSAttributedString typedstream when `text` is NULL), and appends to a local on-disk queue.
4. Flushes the queue in order with `POST webhook_url` (Bearer auth). On failure, it stops and keeps the rest on disk, retrying with exponential backoff (15s → 300s max).

**Outbound (agent → Mac)**
1. The agent sends `POST /enqueue {id, to, body}` to the outbox service.
2. The Mac polls `GET /outbox`, checks each item against the guardrails, sends it with a fixed AppleScript template through Messages.app (`osascript` with recipient/body passed as argv, never `shell=True`), then sends `POST /ack {id, status: sent|failed|rejected}`.
3. The outbox deletes acked items and remembers the ack for 7 days. Re-enqueueing an acked id returns 409.

**Guardrails (enforced on the Mac, not by the agent)**
- Recipient allowlist (`allowlist`), optionally plus numbers in your Contacts (`allow_contacts`).
- Rate limit: `max_sends_per_hour` (default 30).
- Body cap: `max_body_len` (default 1000).
- Idempotent ids: each outbox id is sent at most once. Repeats are re-acked, never re-sent.
- Kill switch: `touch ~/.smsbridge/DISABLED` or set `"enabled": false`.

## Compatibility

So far the bridge has only been tested with Grok Bot. It should work with any agent that can receive a webhook and send authenticated HTTP requests. For inbound, point `webhook_url` at the agent's webhook. For outbound, the agent sends `POST /enqueue` with `Authorization: Bearer $OUTBOX_TOKEN`.

These should work but haven't been tested:

- **Claude Code / Claude Desktop**: inbound, set `webhook_url` to an MCP or HTTP webhook endpoint that feeds your session. Outbound, a tool or MCP server that sends `POST /enqueue` with the outbox token.
- **OpenAI ChatGPT agents / Codex**: inbound, set `webhook_url` to your Actions or Responses relay endpoint. Outbound, a function or Action that sends `POST /enqueue` with the outbox token.
- **OpenClaw and similar self-hosted agents**: inbound, set `webhook_url` to the agent's webhook or hook endpoint. Outbound, an HTTP tool that sends `POST /enqueue` with the outbox token.
- **n8n / Zapier / Make**: inbound, set `webhook_url` to a Webhook trigger node or Catch Hook URL. Outbound, an HTTP Request step that sends `POST /enqueue` with the outbox token.
- **Home Assistant**: inbound, set `webhook_url` to `https://<ha>/api/webhook/<id>` (a webhook automation trigger). Outbound, a `rest_command` that sends `POST /enqueue` with the outbox token.
- **Custom LangChain / LangGraph agents**: inbound, set `webhook_url` to your FastAPI or Flask route that starts a run. Outbound, a tool that sends `POST /enqueue` with the outbox token.

## Install

### 1. Deploy the outbox
Any host that runs Python and Redis works. Environment variables:

| var | meaning |
|---|---|
| `REDIS_URL` | Redis connection string |
| `OUTBOX_TOKEN` | long random secret (`python3 -c 'import secrets;print(secrets.token_urlsafe(32))'`) |
| `PORT` | listen port (default 10000) |
| `PENDING_TTL` | seconds before unacked items expire (default 86400) |

```
pip install -r outbox/requirements.txt
python outbox/app.py
```
Endpoints: `GET /healthz`, `GET /outbox`, `POST /enqueue`, `POST /ack`, `POST /status`. Everything except `/healthz` requires `Authorization: Bearer $OUTBOX_TOKEN`.

### 2. Install the Mac helper
```
xcode-select --install        # if /usr/bin/python3 isn't working yet
bash install.sh
```
This copies the daemon to `~/.smsbridge/`, writes `~/.smsbridge/config.json` (mode 600), builds a small `~/Applications/SMSBridge.app` wrapper, installs `~/Library/LaunchAgents/com.example.smsbridge.plist`, and opens the Full Disk Access settings pane.

### 3. Configure
Edit `~/.smsbridge/config.json` (see `config.example.json`). Fill in **all** of these:
- `webhook_url` / `webhook_auth` — your agent's inbound webhook and its key
- `outbox_url` / `ack_url` — `https://your-outbox.example.com/outbox` and `/ack`
- `outbox_auth` — `Bearer <OUTBOX_TOKEN>`. This is **not** the webhook key.
- `allowlist` — E.164 numbers, such as `+15555550100`

Then restart: `launchctl kickstart -k gui/$(id -u)/com.example.smsbridge`

### 4. Grant permissions
- **Full Disk Access** (see below).
- On the first send, macOS asks "SMSBridge wants to control Messages". Click OK.

### 5. Test
- Inbound: text yourself and confirm it shows up in `~/.smsbridge/log.jsonl` as a `receive` event and at your webhook.
- Outbound: `curl -XPOST -H "Authorization: Bearer $OUTBOX_TOKEN" -d '{"id":"t1","to":"+15555550100","body":"test"}' https://your-outbox.example.com/enqueue`, and it should be delivered within about 15s.

Logs: `~/.smsbridge/log.jsonl` (events) and `~/.smsbridge/stderr.log` (crashes).

## Full Disk Access gotchas

- `sqlite3.DatabaseError: authorization denied` (or `unable to open database file`) on `chat.db` means the running binary doesn't have Full Disk Access.
- `/usr/bin/python3` is a stub. The process TCC actually checks is the CommandLineTools Python. If you run it under Apple's Python directly, grant **both**:
  - `/usr/bin/python3`
  - `/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/Current/Resources/Python.app`

  (In the + file picker, press Cmd-Shift-G to paste a path.) `sys.executable` will mislead you here.
- Apple's Python **cannot** `python3 -m venv --copies` ("This build of python cannot create venvs without using symlinks"), so you can't make an isolated interpreter copy to grant FDA to. The `SMSBridge.app` wrapper built by `install.sh` exists so you can grant FDA to that one app instead of to every Python script on the machine.
- After changing FDA, restart the agent: `launchctl kickstart -k gui/$(id -u)/com.example.smsbridge`.
- `launchctl list | grep smsbridge` shows `PID  status  label`. A status of `-15` just means the previous instance was SIGTERMed on restart.
- No outbound sends? Check that `outbox_url` isn't empty and that `outbox_auth` holds the outbox token, not the webhook key.

## Security model

- The agent never gets a shell on your Mac. It can only receive texts sent to you and *request* a send through the outbox.
- The Mac enforces the rules (allowlist, rate limit, body cap, send-once ids) before anything is sent.
- `chat.db` is opened read-only. Secrets stay in `~/.smsbridge/config.json` (0600) and the outbox's env.
- `log.jsonl` holds message text and numbers in plaintext. Treat it as sensitive.

See **[SECURITY.md](SECURITY.md)** for the threat model, the past security review, a hardening checklist, and how to report a vulnerability privately.

## Uninstall
`bash uninstall.sh`, then remove SMSBridge from Full Disk Access. Run `rm -rf ~/.smsbridge` to delete config and logs.

## Tests
`python3 -m unittest discover tests`

## License
MIT
