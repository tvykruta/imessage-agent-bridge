# Security Policy

## Audit before you run

We strongly recommend that every user does their own security audit before running this code. It has Full Disk Access to read your Messages and sends texts under your identity. The review below is a snapshot, not a guarantee. Read `smsbridge.py`, `send.py`, `outbox/app.py` and the install scripts yourself (or have someone you trust do it), and install the exact commit you reviewed.


This project lets an AI agent read and send iMessage/SMS on your Mac. That is sensitive, so this page explains, in plain language, what the bridge protects, what it does not, and how to report a problem.

## Supported versions

This is a small project with a single maintainer. Only the latest commit on `main` gets security fixes. If you run an older copy, update to `main`.

| Version | Supported |
|---|---|
| `main` (latest) | Yes |
| Anything older | No, please update |

## Reporting a vulnerability

**Please do not open a public issue for security problems.**

Report privately through GitHub:

1. Go to the repository's **Security** tab.
2. Click **Report a vulnerability** (or use [this link](https://github.com/tvykruta/imessage-agent-bridge/security/advisories/new)).
3. Describe the problem, how to reproduce it, and what an attacker could do with it.

Helpful details: the commit you tested, your macOS version, and a minimal proof of concept. Please do not include real phone numbers, message contents, or tokens.

### What to expect

This is maintained on a best-effort basis by one person, so there is no guaranteed SLA. The goals are:

- Acknowledge your report within about a week.
- Agree with you on severity and a fix plan.
- Publish a fix and a GitHub Security Advisory, crediting you if you want.

## Disclosure policy

We follow coordinated disclosure. Please give us a reasonable chance to fix the issue (as a guideline, up to 90 days) before sharing details publicly. Once a fix is on `main`, we publish an advisory describing the issue, its impact, and the fix. If a report turns out not to be a vulnerability, we'll explain why.

## Scope

**In scope**

- `smsbridge.py` (the Mac helper)
- `send.py` (the reference sender) and the ntfy message format
- `outbox/app.py` (the optional self-hosted outbox)
- `install.sh` / `uninstall.sh`
- Anything that lets someone text a number that isn't allowed, queue a text without the key or token, replay an old text, read messages they shouldn't, run commands on the Mac, or get secrets.

**Out of scope**

- Bugs in macOS, Messages.app, Apple's Python, PyNaCl, ntfy, Redis, or your hosting provider (report those upstream).
- Your AI agent's own behavior (see the threat model below).
- Attacks that need someone to already control your Mac user account.
- Denial of service against a self-hosted outbox from someone who already has the token.
- Someone who knows the ntfy topic flooding it with junk (this can delay or drop real texts, but can't send anything).

## Threat model (plain English)

### What the bridge protects

- **Who can be texted.** The Mac decides, not the agent. A send only goes out if the recipient is on your `allowlist` (or in your Contacts, only if you turn `allow_contacts` on). Numbers are compared in full (`+<country><number>`), not by their last digits.
- **How much can be sent.** At most `max_sends_per_hour` (default 30) texts per hour, each at most `max_body_len` (default 1000) characters. Each outbox id is sent at most once.
- **Who can queue a text.** In the default ntfy mode, only someone with the 32-byte `outbox_key`. Each text is encrypted and authenticated (PyNaCl SecretBox, XSalsa20-Poly1305). Notes that fail to decrypt, are older than 10 minutes, more than 2 minutes in the future, or reuse an id (remembered for 24 hours) are dropped. At most 20 bad notes are processed per poll. In self-hosted HTTP mode, only someone with the outbox token (at least 24 characters, compared in constant time).
- **Your Mac's network exposure.** The Mac opens no ports. It only makes outgoing HTTPS requests to your webhook and the relay or outbox.
- **Your Mac's shell.** The agent never gets a shell. The helper runs exactly one external program: `osascript` with a fixed script, with the recipient and message passed as arguments (no `shell=True`, no string building).
- **Your secrets on disk.** `~/.smsbridge/` is owner-only (folder `0700`, files created with umask `077`, `config.json` set to `0600`).

### What the bridge does NOT protect against

- **A compromised Mac.** Anyone running code as your user can read `chat.db`, your config, and your logs directly. The bridge can't help there.
- **A leaked outbox key (or token).** Whoever has the key and the topic can queue texts. The Mac still enforces the allowlist and rate limit, so they can only text allowed numbers, but they can do that. Rotate it if it leaks (see below).
- **A leaked topic alone** is low risk: someone can see when encrypted notes are posted and their size, and can post junk that gets dropped. They can't read, forge or replay texts. Flooding could push you past ntfy.sh's limits, so rotate the topic if it's abused.
- **The public relay's availability.** ntfy.sh is a free, best-effort service. It keeps notes about 12 hours, caps messages at 4096 bytes, and limits about 250 messages a day per IP. Use a self-hosted ntfy server or your own outbox if you need more.
- **A leaked webhook key or a compromised agent endpoint.** Inbound messages are forwarded to your webhook. Whoever controls that endpoint sees your incoming texts.
- **Your agent being tricked.** If an incoming text or web page manipulates your agent (prompt injection) into texting someone *on your allowlist*, the bridge will send it. Keep the allowlist short.
- **Apple Messages itself.** Delivery, encryption, and spam are Apple's responsibility.
- **Message privacy in transit to your agent.** Everything you receive is forwarded to your webhook. Use HTTPS and only point it at an agent you trust.

## Data flow and where secrets live

```
Incoming text -> Messages -> chat.db --(read-only)--> smsbridge.py --(HTTPS + webhook key)--> your agent
Your agent --(HTTPS, encrypted with outbox_key)--> ntfy.sh/<topic> <--(poll)-- smsbridge.py --(decrypt + guardrails, osascript)--> Messages -> recipient
(optional self-hosted mode: agent --(outbox token)--> outbox (Redis) <--(poll + token)-- smsbridge.py)
```

| Secret / data | Where it lives |
|---|---|
| Webhook key, ntfy topic, outbox key (or outbox token) | `~/.smsbridge/config.json` (mode `0600`) on the Mac, and with your agent |
| Queued outbound texts (ntfy mode) | ntfy.sh, encrypted, for up to about 12 hours |
| Outbox token | Outbox service environment variable `OUTBOX_TOKEN` |
| Queued outbound texts (HTTP mode) | Redis on the outbox host, until acked or until `PENDING_TTL` (default 24h) expires them |
| Ack records (status only) | Redis, for 7 days |
| Incoming texts waiting to forward | `~/.smsbridge/inbound_queue.jsonl` |
| Event log | `~/.smsbridge/log.jsonl`, **includes full phone numbers and message text** |

The outbox's own console output masks phone numbers (`***1234`). The Mac's `log.jsonl` does not, so treat it as sensitive.

## Full Disk Access, explained simply

macOS protects your Messages database. To read it, a program needs **Full Disk Access** (FDA). FDA is powerful: a program with it can read most of the files in your account, not just messages.

To keep that small:

- `install.sh` builds a tiny wrapper app, `~/Applications/SMSBridge.app`, and you grant FDA to that app only, not to Terminal or to Python for every script.
- The wrapper only runs `~/.smsbridge/smsbridge.py`. Anyone who can edit that file can run code with FDA, so the installer makes it owner-only (`0700`).
- The helper opens `chat.db` and your AddressBook **read-only**.
- To revoke: remove SMSBridge from System Settings > Privacy & Security > Full Disk Access.

Separately, macOS asks once whether SMSBridge may "control Messages" (Automation). That is what allows sending.

## Hardening checklist

- [ ] Keep `allowlist` short and use full international numbers (`+15555550100`).
- [ ] Leave `allow_contacts` set to `false` unless you really need it.
- [ ] Self-hosted mode: generate a long random outbox token: `python3 -c 'import secrets;print(secrets.token_urlsafe(32))'`.
- [ ] Use different values for the webhook key and the outbox token.
- [ ] Serve the outbox and webhook over HTTPS only.
- [ ] Check permissions: `ls -la ~/.smsbridge` should show only your user.
- [ ] Never commit `config.json` (it's in `.gitignore`).
- [ ] Lower `max_sends_per_hour` to what you actually need.
- [ ] Rotate secrets if you think they leaked, and check `log.jsonl` for unexpected sends. To rotate the ntfy topic and key: remove `ntfy_topic` and `outbox_key` from `config.json`, rerun `bash install.sh`, give your agent the new two lines, then restart.
- [ ] Know the kill switch: `touch ~/.smsbridge/DISABLED` (or set `"enabled": false`) stops all reading and sending within about 15 seconds.
- [ ] Delete old logs if you don't need them: `log.jsonl` keeps message text.

## Past security review

Before the first public release, the code was independently reviewed. All findings below are fixed on `main`.

| # | Severity | Finding | Fix | Status |
|---|---|---|---|---|
| 1 | High | **AppleScript argument injection through the recipient.** A crafted `to` value could be passed to `osascript` in an unsafe form. | Recipients must be a plain phone number or email. Only the normalized value (`+<digits>` or a validated, lowercased email) is passed to `osascript`, as an argument to a fixed script. | Fixed |
| 2 | High | **Allowlist bypass by suffix matching.** Numbers were compared by their last 10 digits, so a number in another country (e.g. `+44...`) with the same ending would pass. | Full-number comparison after normalizing to `+<country><number>`. | Fixed |
| 3 | Medium | **Contacts allowed by default.** `allow_contacts` defaulted to on, so the agent could text anyone in your Contacts. | Default is now `false`. | Fixed |
| 4 | Medium | **Loose file permissions.** Config, state, queue, and log files could be readable by other local users. | `~/.smsbridge` is `0700`, files are created with umask `077`, `config.json` is `0600`, `smsbridge.py` is `0700`. | Fixed |
| 5 | Medium | **Weak outbox token allowed.** | The outbox refuses to start if `OUTBOX_TOKEN` is shorter than 24 characters. Tokens are compared in constant time. | Fixed |
| 6 | Low | **Negative `Content-Length` accepted.** | Requests with a negative length or over 20 KB are rejected. | Fixed |
| 7 | Low | **Unbounded responses.** The Mac would read an outbox response of any size. | Responses are read up to 5 MB, and at most 100 outbox items are processed per poll. | Fixed |

Other safeguards that were already in place: outbox logs mask phone numbers, unacked outbox items expire after `PENDING_TTL`, each outbox id is sent at most once, and there is a local kill switch. A test (`test_no_shell` in `tests/test_smsbridge.py`) checks that the only subprocess call is the fixed `osascript` one.

Thanks to everyone who reports issues responsibly.
