# Agent setup instructions

These instructions are for the AI agent doing the setup. The user only has to say "Set up this repo for me, follow AGENT_SETUP.md" and list the numbers you may text. Ask them for the allowlist if they haven't given it.

Goal: let yourself receive the user's iMessages/SMS and send texts for them from their Mac. Read README.md and SECURITY.md first. Do everything you can yourself. The user should only have to paste one Terminal command, turn on Full Disk Access, and click OK.

1. **Outbox.** Deploy the outbox using the repo's `render.yaml` (Render Blueprint: a free web service plus a Key Value store). If you can't use Render, use any Python host with Redis and set `REDIS_URL` and a random `OUTBOX_TOKEN` of at least 32 characters. Check that `GET /healthz` returns ok. Give the user the outbox URL. Keep the token secret: share it only inside the config below.
2. **Inbound webhook.** Create or choose a webhook of yours that accepts `POST` JSON with a Bearer key. Incoming texts get delivered there.
3. **Config.** Write out the user's complete `~/.smsbridge/config.json`, with every real value filled in: `webhook_url`, `webhook_auth` ("Bearer <webhook key>"), `outbox_url` (<outbox>/outbox), `ack_url` (<outbox>/ack), `outbox_auth` ("Bearer <OUTBOX_TOKEN>"), and an `allowlist` of the numbers the user approved may be texted, in +1XXXXXXXXXX format. Leave `allow_contacts` false.
4. **Mac commands.** Give the user ONE block to paste into Terminal that downloads the repo zip, unzips it, runs `install.sh`, writes the config file above with mode 600, and restarts the bridge (`launchctl kickstart -k gui/$(id -u)/com.example.smsbridge`).
5. **Walk the user through the clicks.** Full Disk Access for SMSBridge.app (in Settings, click +, press Cmd+Shift+G, type ~/Applications, pick SMSBridge.app, switch it on). Then click OK when macOS asks to control Messages.
6. **Test both ways.** Have the user text themselves, and confirm you received it. Then send one test text to an allowlisted number with `POST /enqueue`.

Never text anyone without the user's OK, and never put tokens in a public place.
