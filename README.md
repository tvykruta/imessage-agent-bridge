# imessage-agent-bridge

Give an AI agent narrow, auditable iMessage/SMS access on a Mac — without a shell, and without approving every send by hand.

Two pieces:

- `smsbridge.py` — a ~200-line, stdlib-only Python daemon run by a LaunchAgent on your Mac.
- `outbox/app.py` — a tiny authenticated HTTP queue (Redis-backed) that the agent writes outbound texts into.

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

- The agent never gets a shell on your Mac. It can only (a) receive messages you were already sent and (b) *request* a send through the outbox.
- The Mac is the policy enforcement point: allowlist, rate limit, body cap, and dedupe are all checked locally before anything is sent.
- `chat.db` and AddressBook are opened read-only. There is one `subprocess.run` call (`osascript` with a fixed script, args as argv). `tests/test_no_shell` asserts this.
- Full Disk Access is scoped to one wrapper app.
- Secrets live only in `~/.smsbridge/config.json` (0600) and in the outbox's env. Never commit `config.json`.
- Note: `log.jsonl` contains message bodies and numbers in plaintext. Treat it as sensitive.
- The outbox uses constant-time token comparison, a 20 KB request cap, logs masked numbers only, and expires unacked items.

## Uninstall
`bash uninstall.sh`, then remove SMSBridge from Full Disk Access. Run `rm -rf ~/.smsbridge` to delete config and logs.

## Tests
`python3 -m unittest discover tests`

## License
MIT
