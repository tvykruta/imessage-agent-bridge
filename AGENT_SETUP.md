# Agent setup instructions

These instructions are for the AI agent doing the setup. The user only has to say "Set up imessage-agent-bridge for me, follow AGENT_SETUP.md" and list the numbers you may text. Ask them for that list if they haven't given it.

Goal: receive the user's iMessages and texts, and send texts for them from their Mac. Read README.md and SECURITY.md first. Do everything you can yourself. **No accounts are needed.** Outgoing texts travel encrypted through the free public relay ntfy.sh.

The user's whole job is: paste one command into Terminal, answer three prompts, turn on Full Disk Access, and paste two lines back to you.

## Rules that prevent the common setup failures

- **Never put secrets or placeholders in commands.** Don't write `PASTE_KEY_HERE` or ask the user to edit a command. The installer asks for the webhook key at a hidden prompt and rejects placeholders.
- **Don't write your own shell prompts or edit `config.json` with inline scripts.** macOS Terminal uses zsh, where `read -p` fails, so those edits fail without saying so. Pass settings to `install.sh` as flags. It asks for anything missing.
- **Don't pipe the installer into `bash`** (`curl ... | bash`). It needs the keyboard for the prompts.
- **Use a full `https://` webhook URL.** The installer refuses to finish without a valid URL and a non-empty allowlist, and it sends a test message to your webhook before saving.

## Steps

1. **Inbound webhook.** Create or choose a webhook of yours that accepts `POST` JSON with a Bearer key. One `POST` per incoming message, body: `{"rowid": 123, "time": "2026-10-11T15:04:05-05:00", "handle": "+15125550100", "is_from_me": false, "text": "hi", "chat_guid": "iMessage;-;+15125550100"}`. `handle` can be an email address for iMessage. Each request carries `Idempotency-Key: imab-<rowid>`. Ignore keys you've already seen, because the Mac retries until it gets a 2xx. The installer's test message uses `rowid` 0 and `handle` `smsbridge-test`. Ignore it.

2. **Give the user this one command,** with your real webhook URL and the approved numbers filled in (no key):
   ```
   cd ~ && curl -fsSL -o imab.zip https://github.com/tvykruta/imessage-agent-bridge/archive/refs/heads/main.zip && unzip -oq imab.zip && bash imessage-agent-bridge-main/install.sh --webhook-url "https://YOUR-WEBHOOK" --allow "+15125550100,+15125550101"
   ```
   Tell the user what will happen, in plain words:
   - If the Mac has no Python, the installer stops and explains two options. Either install Apple's Command Line Tools (`xcode-select --install`, which is not the full Xcode app) or the python.org installer. Then run the same command again.
   - It asks for the **webhook key**. Typing is hidden. They paste the key you give them (send it privately) and press Return. It then sends a test message to your webhook. Confirm you received it.
   - It opens Full Disk Access. They click **+**, press **Cmd+Shift+G**, type `~/Applications`, choose **SMSBridge.app**, switch it on, then press Return in Terminal.
   - It runs a health check (`doctor`) and prints two lines: `NTFY_TOPIC=...` and `OUTBOX_KEY=...`.

3. **Get the two secrets.** Ask the user to paste the `NTFY_TOPIC` and `OUTBOX_KEY` lines to you. Store them as secrets and never post them publicly. The key is what authorizes sends.

4. **Sending a text.** Use `send.py` from this repo (`pip install pynacl`):
   ```
   NTFY_TOPIC=imab-... OUTBOX_KEY=... python3 send.py +15555550100 "Test from my agent"
   ```
   Or in code: `from send import send; send(to, body, topic, key)`. Wire format: JSON `{"id": <unique>, "to", "body", "ts": <unix seconds>}`, encrypted with PyNaCl `SecretBox(base64url-decoded key)` and a random 24-byte nonce, then base64 of nonce+ciphertext as the plain-text body of `POST https://ntfy.sh/<topic>`. The Mac drops notes that fail to decrypt, are older than 10 minutes, are more than 2 minutes ahead of the Mac's clock, or reuse an id. Keep the encoded message under 4096 bytes (about 1,000 characters).

5. **Test both ways.** Ask the user to text themselves, and confirm you received it. Then send one test text to an allowlisted number. The first send makes macOS ask "SMSBridge wants to control Messages". The user clicks OK once.

6. **If anything fails,** ask the user to run `~/.smsbridge/smsbridge doctor` and paste the output. It checks Python, certificates, the Mac's clock, settings, the webhook (test POST), ntfy.sh, Full Disk Access and Messages permission, and says how to fix each problem. In `~/.smsbridge/log.jsonl`, `ntfy_reject` lines include a `hint` plus `mac_time`, `note_ts` and `relay_time`, so you can tell whose clock is wrong.

7. **Changing settings later:** `~/.smsbridge/smsbridge setup` (same prompts). Rerunning the install command is also safe. It keeps the topic and key.

Optional: `"ntfy_acks": true` makes the Mac publish encrypted `{"id","status","error","ts"}` receipts to `<topic>-acks`. `"allow_contacts": true` also allows numbers in Contacts. `"allow_all": true` allows **any** number. Only turn it on if the user explicitly asks, and warn them first. For a self-hosted queue instead of ntfy.sh, see "Advanced: host your own queue" in README.md.

Never text anyone without the user's OK, and never put keys in a public place.
