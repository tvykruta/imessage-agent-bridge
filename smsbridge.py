#!/usr/bin/env python3
"""SMSBridge: narrow read/send-SMS helper for macOS.
Commands: (none) run the bridge | setup  interactive settings | doctor  check everything.
Capabilities: read ~/Library/Messages/chat.db (read-only), send via osascript->Messages.
Outbound texts arrive encrypted over an ntfy.sh topic (default; needs PyNaCl) or from a
self-hosted HTTP outbox (stdlib only). Nothing else is executed."""
import base64, getpass, json, ssl, os, re, sqlite3, subprocess, sys, time, glob, urllib.parse, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta

HOME = os.path.expanduser("~")
BASE = os.path.join(HOME, ".smsbridge")
CONFIG = os.path.join(BASE, "config.json")
STATE = os.path.join(BASE, "state.json")
QUEUE = os.path.join(BASE, "inbound_queue.jsonl")
LOG = os.path.join(BASE, "log.jsonl")
KILL = os.path.join(BASE, "DISABLED")
STATUS = os.path.join(BASE, "status.json")
CHATDB = os.path.join(HOME, "Library/Messages/chat.db")
APPLE_EPOCH = 978307200
DEFAULTS = {"enabled": True, "webhook_url": "", "webhook_auth": "", "outbox_url": "",
            "ack_url": "", "outbox_auth": "", "allowlist": [], "allow_contacts": False,
            "max_sends_per_hour": 30, "max_body_len": 1000, "poll_seconds": 15,
            "tz_offset_hours": None, "allow_all": False, "outbox_transport": "", "ntfy_server": "https://ntfy.sh",
            "ntfy_topic": "", "outbox_key": "", "ntfy_acks": False}
MAX_AGE, MAX_SKEW = 600, 120          # reject items older than 10 min / >2 min in the future
SEEN_TTL, MAX_BAD_PER_POLL = 86400, 20
SCRIPT = '''on run argv
  set theTo to item 1 of argv
  set theBody to item 2 of argv
  tell application "Messages"
    try
      set svc to 1st account whose service type = SMS
    on error
      set svc to 1st account whose service type = iMessage
    end try
    send theBody to participant theTo of svc
  end tell
end run'''

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def load_json(p, d):
    try:
        with open(p) as f: return json.load(f)
    except (OSError, ValueError): return d

def save_json(p, obj):
    tmp = p + ".tmp"
    with open(tmp, "w") as f: json.dump(obj, f)
    os.replace(tmp, p)

def log(event, **kw):
    kw.update(ts=now_iso(), event=event)
    with open(LOG, "a") as f: f.write(json.dumps(kw) + "\n")

def load_config(path=CONFIG):
    c = dict(DEFAULTS); c.update(load_json(path, {})); return c

def disabled(cfg, kill_path=KILL):
    return (not cfg.get("enabled", True)) or os.path.exists(kill_path)

# ---------- attributedBody decoding ----------
def decode_attributed_body(blob):
    """Extract the plain string from an NSAttributedString typedstream blob."""
    if not blob: return None
    blob = bytes(blob)
    i = blob.find(b"NSString")
    if i < 0: return None
    j = blob.find(b"\x01\x2b", i)  # '+' marks the C string that follows
    if j < 0: return None
    k = j + 2
    n = blob[k]
    if n == 0x81: ln = int.from_bytes(blob[k+1:k+3], "little"); k += 3
    elif n == 0x82: ln = int.from_bytes(blob[k+1:k+5], "little"); k += 5
    else: ln = n; k += 1
    return blob[k:k+ln].decode("utf-8", "replace")

def apple_time_to_iso(v, offset_hours=None):
    if v is None: return None
    secs = v / 1e9 if v > 1e11 else v  # ns since High Sierra
    dt = datetime.fromtimestamp(secs + APPLE_EPOCH, timezone.utc)
    tz = timezone(timedelta(hours=offset_hours)) if offset_hours is not None else None
    return dt.astimezone(tz).isoformat()  # None -> Mac local time

# ---------- inbound ----------
QUERY = """SELECT m.ROWID, m.date, h.id, m.is_from_me, m.text, m.attributedBody, c.guid
FROM message m LEFT JOIN handle h ON m.handle_id=h.ROWID
LEFT JOIN chat_message_join cmj ON cmj.message_id=m.ROWID
LEFT JOIN chat c ON c.ROWID=cmj.chat_id
WHERE m.ROWID > ? ORDER BY m.ROWID LIMIT 200"""

def read_new(db_path, last_rowid, offset_hours=None):
    con = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    try:
        rows = con.execute(QUERY, (last_rowid,)).fetchall()
    finally:
        con.close()
    out, seen = [], set()
    for rid, d, handle, fm, text, ab, guid in rows:
        if rid in seen: continue
        seen.add(rid)
        out.append({"rowid": rid, "time": apple_time_to_iso(d, offset_hours), "handle": handle,
                    "is_from_me": bool(fm), "text": text if text is not None else decode_attributed_body(ab),
                    "chat_guid": guid})
    return out

_SSL = None
def ssl_context():
    """Use certifi's CA bundle when present. python.org Python ships without trusted
    certificates until 'Install Certificates.command' is run; certifi avoids that step."""
    global _SSL
    if _SSL is None:
        try:
            import certifi; _SSL = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            _SSL = ssl.create_default_context()
    return _SSL

def explain(e):
    """Plain-English hint for common network errors."""
    m = str(e)
    if "CERTIFICATE_VERIFY_FAILED" in m:
        return m + " (Python has no trusted certificates: rerun install.sh, which installs certifi)"
    if "nodename nor servname" in m or "Name or service not known" in m:
        return m + " (the server name could not be found: check webhook_url, or that the Mac is online)"
    if "HTTP Error 401" in m or "HTTP Error 403" in m:
        return m + " (the server refused the key: check webhook_auth)"
    return m

def http(method, url, auth, payload=None, timeout=20, headers=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if auth: req.add_header("Authorization", auth)
    for k, v in (headers or {}).items(): req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as r:
        body = r.read(5_000_000)
    return json.loads(body) if body.strip() else None

def flush_queue(cfg, post=http):
    """POST queued messages in order; stop at first failure (kept on disk)."""
    if not os.path.exists(QUEUE) or check_webhook(cfg): return 0
    with open(QUEUE) as f: items = [l for l in f if l.strip()]
    sent = 0
    for line in items:
        try:
            m = json.loads(line)
            # Stable per-message key so receivers can drop duplicates after retries.
            post("POST", cfg["webhook_url"], cfg["webhook_auth"], m,
                 headers={"Idempotency-Key": "imab-%s" % m.get("rowid")}); sent += 1
        except Exception as e:
            log("webhook_error", error=explain(e)[:300]); break
    with open(QUEUE + ".tmp", "w") as f: f.writelines(items[sent:])
    os.replace(QUEUE + ".tmp", QUEUE)
    return len(items) - sent

def poll_inbound(cfg, state):
    msgs = read_new(CHATDB, state.get("last_rowid", 0), cfg.get("tz_offset_hours"))
    if msgs:
        with open(QUEUE, "a") as f:
            for m in msgs:
                f.write(json.dumps(m) + "\n")
                log("receive", rowid=m["rowid"], handle=m["handle"], is_from_me=m["is_from_me"], text=m["text"])
        state["last_rowid"] = msgs[-1]["rowid"]; save_json(STATE, state)

# ---------- outbound ----------
def norm(num):
    """Canonical E.164-style '+<digits>' (10 digits assumed US/NANP). Full number compared, no suffix matching."""
    d = re.sub(r"\D", "", str(num or ""))
    if len(d) == 10: d = "1" + d
    return "+" + d if 8 <= len(d) <= 15 else ""

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+][A-Za-z0-9._%+-]*@(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,}$")

def canon_recipient(to):
    to = str(to or "").strip()
    if "@" in to: return to.lower() if EMAIL_RE.match(to) else ""
    # Reject anything that isn't a plain phone number (prevents argv/option smuggling).
    if not re.fullmatch(r"[+0-9 ()./-]{7,25}", to): return ""
    return norm(to)

def contact_numbers():
    nums = set()
    for db in glob.glob(os.path.join(HOME, "Library/Application Support/AddressBook/Sources/*/AddressBook-v22.abcddb")) + \
              glob.glob(os.path.join(HOME, "Library/Application Support/AddressBook/AddressBook-v22.abcddb")):
        try:
            con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
            nums |= {norm(r[0]) for r in con.execute("SELECT ZFULLNUMBER FROM ZABCDPHONENUMBER") if r[0]}
            con.close()
        except sqlite3.Error: pass
    return nums

def check_send(item, cfg, state, contacts_fn=contact_numbers, now=None):
    """Return None if allowed, else reason string."""
    now = now or time.time()
    to, body = str(item.get("to", "")), item.get("body")
    if not isinstance(body, str) or not body.strip(): return "empty_body"
    if len(body) > cfg["max_body_len"]: return "body_too_long"
    allowed = {canon_recipient(a) for a in cfg["allowlist"]} - {""}
    key = canon_recipient(to)
    if not key: return "bad_recipient"
    if not cfg.get("allow_all") and key not in allowed and not (cfg["allow_contacts"] and key in {norm(n) for n in contacts_fn()}):
        return "not_allowlisted"
    recent = [t for t in state.get("send_times", []) if now - t < 3600][-1000:]
    state["send_times"] = recent
    if len(recent) >= cfg["max_sends_per_hour"]: return "rate_limited"
    item["_to"] = key  # only the canonical recipient is ever passed to osascript
    return None

def send_sms(to, body):
    # Fixed template; recipient/body passed as argv, no shell.
    r = subprocess.run(["/usr/bin/osascript", "-e", SCRIPT, to, body], capture_output=True, text=True, timeout=60)
    if r.returncode != 0: raise RuntimeError(r.stderr.strip()[:300])

def process_items(items, cfg, state, sender=send_sms, contacts_fn=contact_numbers):
    """Run guardrails and send. Returns [(id, status, error)]. Each id is sent at most once."""
    sent_ids = state.setdefault("sent_ids", {})
    state.setdefault("send_times", [])
    out = []
    for it in items:
        iid = str(it.get("id", ""))
        if not iid: continue
        if iid in sent_ids:
            status, err = sent_ids[iid], None  # re-ack only, never resend
        else:
            err = check_send(it, cfg, state, contacts_fn)
            if err is None:
                try:
                    sender(it["_to"], it["body"]); status = "sent"
                    state["send_times"].append(time.time())
                except Exception as e:
                    status, err = "failed", str(e); state["last_send_error"] = err[:300]
            else:
                status = "rejected"
            sent_ids[iid] = status; save_json(STATE, state)
            log("send", id=iid, to=it.get("to"), body=it.get("body"), status=status, error=err)
        out.append((iid, status, err))
    return out

def transport(cfg):
    t = cfg.get("outbox_transport")
    if t in ("ntfy", "http"): return t
    return "http" if cfg.get("outbox_url") and not cfg.get("ntfy_topic") else "ntfy"

# ---------- ntfy transport (encrypted; relay is untrusted) ----------
def _box(key_b64):
    try:
        from nacl.secret import SecretBox
    except ImportError:
        raise RuntimeError("PyNaCl missing: rerun install.sh (ntfy mode needs it)")
    key = base64.urlsafe_b64decode(key_b64 + "=" * (-len(key_b64) % 4))
    if len(key) != 32: raise ValueError("outbox_key must be 32 bytes, base64")
    return SecretBox(key)

def encrypt_item(key_b64, item):
    """Reference sender side: returns the ntfy message body for {id,to,body}."""
    return encrypt_json(key_b64, {"id": item["id"], "to": item["to"], "body": item["body"],
                                  "ts": int(item.get("ts", time.time()))})

def encrypt_json(key_b64, obj):
    import nacl.utils
    return base64.b64encode(bytes(_box(key_b64).encrypt(json.dumps(obj).encode(), nacl.utils.random(24)))).decode()

def decrypt_item(box, text, state, now=None):
    """Return the item dict or raise ValueError(reason). Checks MAC, age and replay."""
    now = now or time.time()
    try:
        raw = base64.b64decode(text.strip(), validate=True)
        it = json.loads(box.decrypt(raw).decode())
    except Exception:
        raise ValueError("bad_ciphertext")
    if not isinstance(it, dict) or not isinstance(it.get("ts"), (int, float)): raise ValueError("bad_payload")
    if it["ts"] < now - MAX_AGE: raise ValueError("stale")
    if it["ts"] > now + MAX_SKEW: raise ValueError("future")
    # (callers log mac_time and note_ts on every reject, so clock problems are visible)
    seen = state.setdefault("seen_ids", {})
    for k in [k for k, t in seen.items() if now - t > SEEN_TTL]: del seen[k]
    iid = str(it.get("id", ""))
    if not iid: raise ValueError("bad_payload")
    if iid in seen or iid in state.get("sent_ids", {}): raise ValueError("replay")
    seen[iid] = now
    return it

REJECT_HINTS = {
    "stale": "note older than 10 minutes: the Mac was asleep/offline, or a clock is behind",
    "future": "note dated ahead of the Mac clock: compare mac_time, note_ts and relay_time to see whose clock is wrong",
    "bad_ciphertext": "wrong outbox_key on the agent side",
    "replay": "same id sent twice; ignored on purpose"}

def _peek_ts(box, text):
    try: return json.loads(box.decrypt(base64.b64decode(text.strip())).decode()).get("ts")
    except Exception: return None

def http_text(method, url, data=None, timeout=20, headers=None):
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in (headers or {}).items(): req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as r:
        return r.read(5_000_000).decode("utf-8", "replace")

def poll_ntfy(cfg, state, fetch=http_text, sender=send_sms, contacts_fn=contact_numbers, now=None):
    topic, server = cfg["ntfy_topic"], cfg["ntfy_server"].rstrip("/")
    if not topic or not cfg["outbox_key"]: return
    box = _box(cfg["outbox_key"])
    since = state.get("ntfy_since") or str(int(now or time.time()) - MAX_AGE)  # first run: last 10 min only
    url = "%s/%s/json?poll=1&since=%s" % (server, urllib.parse.quote(topic, safe=""), urllib.parse.quote(since, safe=""))
    lines = [l for l in fetch("GET", url).splitlines() if l.strip()][:200]
    items, bad = [], 0
    for line in lines:
        try: ev = json.loads(line)
        except ValueError: continue
        if ev.get("event") != "message": continue
        state["ntfy_since"] = str(ev.get("id") or since)
        if bad >= MAX_BAD_PER_POLL: continue
        try:
            items.append(decrypt_item(box, ev.get("message", ""), state, now))
        except ValueError as e:
            bad += 1
            log("ntfy_reject", ntfy_id=ev.get("id"), reason=str(e), mac_time=int(now or time.time()),
                note_ts=_peek_ts(box, ev.get("message", "")), relay_time=ev.get("time"),
                hint=REJECT_HINTS.get(str(e), ""))
    save_json(STATE, state)
    for iid, status, err in process_items(items, cfg, state, sender, contacts_fn):
        if cfg.get("ntfy_acks"):
            try:
                body = encrypt_json(cfg["outbox_key"], {"id": iid, "status": status, "error": err, "ts": int(time.time())})
                fetch("POST", "%s/%s" % (server, urllib.parse.quote(topic + "-acks", safe="")), body.encode())
            except Exception as e: log("ack_error", id=iid, error=str(e)[:200])

# ---------- http transport (self-hosted outbox/app.py) ----------
def poll_outbox(cfg, state, get=http, sender=send_sms, contacts_fn=contact_numbers):
    if not cfg["outbox_url"]: return
    items = get("GET", cfg["outbox_url"], cfg["outbox_auth"]) or []
    if not isinstance(items, list): return
    items = [it for it in items[:100] if isinstance(it, dict)]
    for iid, status, err in process_items(items, cfg, state, sender, contacts_fn):
        if cfg["ack_url"]:
            try: get("POST", cfg["ack_url"], cfg["outbox_auth"], {"id": iid, "status": status, "error": err})
            except Exception as e: log("ack_error", id=iid, error=str(e)[:200])

def poll_outbound(cfg, state):
    return poll_ntfy(cfg, state) if transport(cfg) == "ntfy" else poll_outbox(cfg, state)

# ---------- config validation, setup, doctor ----------
PLACEHOLDER_RE = re.compile(r"PASTE|YOUR_|REPLACE|EXAMPLE|<|>|\.\.\.|GENERATED_BY", re.I)

def check_webhook(cfg):
    """Return a plain-English problem with webhook_url/webhook_auth, or None."""
    u = str(cfg.get("webhook_url") or "").strip()
    if not u: return "webhook_url is empty: run 'smsbridge setup' (or install.sh) and enter your agent's webhook URL"
    pu = urllib.parse.urlparse(u)
    if pu.scheme != "https" or not pu.hostname or "." not in pu.hostname:
        return "webhook_url must be a full https:// address, got %r" % u[:80]
    if PLACEHOLDER_RE.search(pu.hostname): return "webhook_url still contains a placeholder"
    a = str(cfg.get("webhook_auth") or "")
    if a and (PLACEHOLDER_RE.search(a) or not re.fullmatch(r"\S+( \S+)?", a)):
        return "webhook_auth looks like a placeholder or has stray spaces: run 'smsbridge setup' to re-enter the key"
    return None

def config_problems(cfg):
    """Everything that would stop the bridge from working. Empty list = good."""
    out = []
    w = check_webhook(cfg)
    if w: out.append(w)
    if not cfg.get("allow_all") and not cfg.get("allow_contacts"):
        good = [a for a in cfg.get("allowlist") or [] if canon_recipient(a)]
        if not good: out.append("allowlist is empty: add the numbers your agent may text (run 'smsbridge setup')")
        bad = [a for a in cfg.get("allowlist") or [] if not canon_recipient(a)]
        if bad: out.append("allowlist has entries that are not phone numbers or emails: %s" % bad[:3])
    if transport(cfg) == "ntfy":
        if not cfg.get("ntfy_topic") or PLACEHOLDER_RE.search(cfg["ntfy_topic"]): out.append("ntfy_topic missing: rerun install.sh")
        try: _box(cfg.get("outbox_key") or "")
        except Exception: out.append("outbox_key missing or invalid: rerun install.sh")
    return out

def write_config(cfg, path=None):
    path = path or CONFIG
    fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f: json.dump(cfg, f, indent=2)
    os.replace(path + ".tmp", path)

def test_webhook(cfg, post=None):
    """Send one test POST. Returns None on success, else a plain-English error."""
    post = post or http
    try:
        post("POST", cfg["webhook_url"], cfg["webhook_auth"],
             {"rowid": 0, "time": now_iso(), "handle": "smsbridge-test", "is_from_me": False,
              "text": "SMSBridge test message (safe to ignore)", "chat_guid": "smsbridge-test"},
             headers={"Idempotency-Key": "imab-test-%d" % int(time.time())})
        return None
    except Exception as e:
        return explain(e)

def apply_answers(cfg, url=None, key=None, allow=None):
    """Validate and merge setup answers. Raises ValueError with a readable message."""
    c = dict(cfg)
    if url is not None: c["webhook_url"] = url.strip()
    if key is not None:
        key = key.strip()
        if key.lower().startswith("bearer "): key = key[7:].strip()
        if not key or PLACEHOLDER_RE.search(key) or re.search(r"\s", key):
            raise ValueError("that does not look like a real key (empty, has spaces, or is a placeholder)")
        c["webhook_auth"] = "Bearer " + key
    if allow is not None:
        nums = [x.strip() for x in re.split(r"[,;\n]+", allow) if x.strip()]
        canon = [canon_recipient(x) for x in nums]
        if not nums: raise ValueError("enter at least one number")
        if "" in canon: raise ValueError("not a phone number or email: %s" % nums[canon.index("")])
        c["allowlist"] = canon
    w = check_webhook(c)
    if w: raise ValueError(w)
    return c

def _ask(prompt, secret=False):
    try:
        return getpass.getpass(prompt) if secret else input(prompt)
    except EOFError:
        sys.exit("No answer received. Run this in Terminal: ~/.smsbridge/venv/bin/python3 ~/.smsbridge/smsbridge.py setup")

def setup(argv, path=None):
    """Interactive, zsh/bash-agnostic setup. Flags: --webhook-url URL --allow '+1..,+1..' --no-test.
    The webhook key is always typed at a hidden prompt, never passed on the command line."""
    path = path or CONFIG
    args = dict(zip(argv[::2], argv[1::2]))
    cfg = load_config(path)
    url = args.get("--webhook-url") or cfg.get("webhook_url") or ""
    while True:
        try:
            if check_webhook({"webhook_url": url}):
                url = _ask("Your agent's webhook URL (https://...): ")
            cfg2 = apply_answers(cfg, url=url); break
        except ValueError as e:
            print("  " + str(e)); url = ""
    cfg = cfg2
    while True:
        try:
            k = _ask("Webhook key from your agent (typing is hidden, press Enter when done): ", secret=True)
            cfg2 = apply_answers(cfg, key=k)
        except ValueError as e:
            print("  " + str(e)); continue
        if "--no-test" in argv: break
        print("  Sending a test message to your agent...")
        err = test_webhook(cfg2)
        if err is None: print("  OK, your agent received it."); break
        print("  Test failed: " + err)
        if _ask("  Type r to re-enter the key, or s to save anyway: ").strip().lower() != "s":
            if "401" not in err and "403" not in err:
                try: cfg = apply_answers(cfg, url=_ask("Webhook URL (Enter to keep): ").strip() or cfg["webhook_url"])
                except ValueError as e2: print("  " + str(e2))
            continue
        break
    cfg = cfg2
    allow = args.get("--allow")
    while True:
        try:
            if allow is None:
                allow = _ask("Numbers your agent may text, comma separated (e.g. +15125550100): ")
            cfg = apply_answers(cfg, allow=allow); break
        except ValueError as e:
            print("  " + str(e)); allow = None
    write_config(cfg, path)
    print("Saved %s. Allowed: %s" % (path, ", ".join(cfg["allowlist"])))
    return cfg

def doctor(cfg=None, out=print, status_path=None, fetch=None, post=None):
    """Check everything that commonly breaks. Returns the number of problems."""
    cfg = cfg or load_config()
    fetch = fetch or http_text
    bad = [0]
    def res(ok, what, fix=""):
        out(("  ok    " if ok else "  FIX   ") + what + ("" if ok else "\n        -> " + fix))
        if not ok: bad[0] += 1
    out("SMSBridge doctor")
    res(sys.version_info >= (3, 8), "Python %s at %s" % (sys.version.split()[0], sys.executable), "rerun install.sh")
    try:
        import nacl.secret  # noqa
        res(True, "PyNaCl installed")
    except ImportError:
        res(False, "PyNaCl installed", "rerun install.sh while online")
    try:
        import certifi; res(True, "certificates (certifi %s)" % certifi.__version__)
    except ImportError:
        res(False, "certificates (certifi)", "rerun install.sh while online")
    probs = config_problems(cfg)
    res(not probs, "config.json settings", "; ".join(probs))
    if cfg.get("allow_all"): out("  note  allow_all is on: your agent can text ANY number")
    server = cfg.get("ntfy_server", "https://ntfy.sh").rstrip("/")
    if transport(cfg) == "ntfy":
        try:
            fetch("GET", server + "/v1/health"); res(True, "can reach " + server)
        except Exception as e:
            res(False, "can reach " + server, explain(e))
    # Clock: compare with the relay's clock (HTTP Date header).
    try:
        req = urllib.request.Request(server, method="HEAD")
        with urllib.request.urlopen(req, timeout=10, context=ssl_context()) as r: d = r.headers.get("Date")
        from email.utils import parsedate_to_datetime
        skew = time.time() - parsedate_to_datetime(d).timestamp()
        res(abs(skew) < 60, "Mac clock (off by %ds)" % skew, "turn on 'Set time and date automatically' in System Settings > General > Date & Time")
    except Exception:
        out("  skip  clock check (could not reach %s)" % server)
    if not check_webhook(cfg):
        err = test_webhook(cfg, post)
        res(err is None, "webhook test POST", err or "")
    st = load_json(status_path or STATUS, None)
    if not st:
        res(False, "bridge running", "start it: launchctl kickstart -k gui/$(id -u)/com.example.smsbridge")
    else:
        age = time.time() - st.get("time", 0)
        res(age < 120, "bridge running (last check %ds ago)" % age,
            "start it: launchctl kickstart -k gui/$(id -u)/com.example.smsbridge, then see ~/.smsbridge/stderr.log")
        e = st.get("read_error") or ""
        res(not e, "Full Disk Access (bridge can read Messages)",
            "System Settings > Privacy & Security > Full Disk Access: add ~/Applications/SMSBridge.app and switch it on, then restart the bridge (%s)" % e[:80])
        se = st.get("send_error") or ""
        res("not allowed" not in se.lower() and "-1743" not in se, "Messages automation permission",
            "System Settings > Privacy & Security > Automation: allow SMSBridge to control Messages")
        if st.get("errors"): out("  last errors: " + "; ".join(st["errors"])[:400])
    out("All good." if not bad[0] else "%d thing(s) to fix." % bad[0])
    return bad[0]

def main():
    os.umask(0o077)  # state/log/queue contain message text: owner-only
    os.makedirs(BASE, mode=0o700, exist_ok=True)
    backoff, last_probs = 0, None
    log("start", mac_time=now_iso(), python=sys.executable)
    while True:
        cfg = load_config()
        if disabled(cfg):
            time.sleep(15); continue
        probs = config_problems(cfg)
        if probs != last_probs:
            if probs: log("config_error", problems=probs)
            last_probs = probs
        state = load_json(STATE, {})
        if "last_rowid" not in state:  # first run: start from now, don't replay history
            con = sqlite3.connect("file:%s?mode=ro" % CHATDB, uri=True)
            state["last_rowid"] = con.execute("SELECT IFNULL(MAX(ROWID),0) FROM message").fetchone()[0]
            con.close(); save_json(STATE, state); log("start", last_rowid=state["last_rowid"])
        ok, status = True, {"time": time.time(), "mac_time": now_iso(), "errors": probs[:]}
        try: poll_inbound(cfg, state)
        except Exception as e:
            log("read_error", error=str(e)[:300]); ok = False; status["read_error"] = str(e)[:300]
        if flush_queue(cfg): ok = False
        try: poll_outbound(cfg, state)
        except Exception as e:
            log("outbox_error", error=explain(e)[:300]); ok = False; status["errors"].append(explain(e)[:200])
        status["send_error"] = state.get("last_send_error", "")
        save_json(STATUS, status)
        backoff = 0 if ok else min(300, max(15, backoff * 2))
        time.sleep(backoff or cfg["poll_seconds"])

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "setup": setup(sys.argv[2:])
    elif cmd == "doctor": sys.exit(1 if doctor() else 0)
    elif cmd == "check":  # non-interactive: exit 1 and list problems if config is incomplete
        p = config_problems(load_config())
        for x in p: print(" - " + x)
        sys.exit(1 if p else 0)
    elif cmd: sys.exit(__doc__)
    else: main()
