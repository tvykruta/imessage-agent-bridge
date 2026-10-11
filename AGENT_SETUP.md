# Agent setup instructions

These instructions are for the AI agent doing the setup. The user only has to say "Set up this repo for me, follow AGENT_SETUP.md" and list the numbers you may text. Ask them for the allowlist if they haven't given it.

Goal: let yourself receive the user's iMessages/SMS and send texts for them from their Mac. Read README.md and SECURITY.md first. Do everything you can yourself. The user should only have to paste one Terminal command, turn on Full Disk Access, click OK, and paste back two lines the installer prints. **No accounts are needed.** Outgoing texts travel encrypted through the free public relay ntfy.sh.

1. **Inbound webhook.** Create or choose a webhook of yours that accepts `POST` JSON with a Bearer key. Incoming texts get delivered there, one `POST` per message, with this JSON body: `{"rowid": 123, "time": "2026-10-11T15:04:05", "handle": "+15125550100", "is_from_me": false, "text": "hi", "chat_guid": "iMessage;-;+15125550100"}`. `handle` can also be an email address for iMessage. Return any 2xx status to accept it. Anything else makes the Mac retry with backoff.
2. **Mac commands.** Give the user ONE block to paste into Terminal that downloads the repo zip (`https://github.com/tvykruta/imessage-agent-bridge/archive/refs/heads/main.zip`), unzips it, runs `bash install.sh`, then sets `webhook_url`, `webhook_auth` ("Bearer <webhook key>") and `allowlist` (numbers the user approved, in +1XXXXXXXXXX format) in `~/.smsbridge/config.json`, for example with `/usr/bin/python3 -c` editing the JSON, keeping mode 600, and finally restarts the bridge (`launchctl kickstart -k gui/$(id -u)/com.example.smsbridge`). Leave `allow_contacts` false. Do not overwrite `ntfy_topic` or `outbox_key`.
3. **Get the two secrets.** `install.sh` prints `NTFY_TOPIC=...` and `OUTBOX_KEY=...` once. Ask the user to paste those two lines to you. Store them as secrets. Never post them anywhere public. The key is what authorizes sends.
4. **Walk the user through the clicks.** Full Disk Access for SMSBridge.app (in Settings, click +, press Cmd+Shift+G, type ~/Applications, pick SMSBridge.app, switch it on). Then click OK when macOS asks to control Messages.
5. **Sending a text.** Use `send.py` from this repo (needs `pip install pynacl`):
   ```
   NTFY_TOPIC=imab-... OUTBOX_KEY=... python3 send.py +15555550100 "Test from my agent"
   ```
   Or in your own code: `from send import send; send(to, body, topic, key)`. The wire format: JSON `{"id": <unique>, "to", "body", "ts": <unix seconds>}`, encrypted with PyNaCl `SecretBox(base64url-decoded key)` and a random 24-byte nonce, base64 of nonce+ciphertext as the plain-text body of `POST https://ntfy.sh/<topic>`. The Mac drops anything that fails to decrypt, is older than 10 minutes, more than 2 minutes in the future, or reuses an id. Keep the encoded message under 4096 bytes (roughly 1000 characters of text).
6. **Test both ways.** Have the user text themselves, and confirm you received it. Then send one test text to an allowlisted number with `send.py` and ask the user to confirm it arrived. If it didn't, ask them for the last lines of `~/.smsbridge/log.jsonl`.
7. **Optional receipts.** If the user sets `"ntfy_acks": true`, the Mac publishes encrypted `{"id","status","error","ts"}` receipts to `<topic>-acks`. Read them with `GET https://ntfy.sh/<topic>-acks/json?poll=1&since=10m` and decrypt with the same key.

**Self-hosted queue instead:** if the user prefers their own server, see "Advanced: host your own queue" in README.md.

Never text anyone without the user's OK, and never put keys in a public place.
