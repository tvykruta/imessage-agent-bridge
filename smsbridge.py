#!/usr/bin/env python3
"""SMSBridge: narrow read/send-SMS helper for macOS. Stdlib only.
Capabilities: read ~/Library/Messages/chat.db (read-only), send via osascript->Messages.
Nothing else is executed."""
import json, os, re, sqlite3, subprocess, sys, time, glob, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta

HOME = os.path.expanduser("~")
BASE = os.path.join(HOME, ".smsbridge")
CONFIG = os.path.join(BASE, "config.json")
STATE = os.path.join(BASE, "state.json")
QUEUE = os.path.join(BASE, "inbound_queue.jsonl")
LOG = os.path.join(BASE, "log.jsonl")
KILL = os.path.join(BASE, "DISABLED")
CHATDB = os.path.join(HOME, "Library/Messages/chat.db")
APPLE_EPOCH = 978307200
DEFAULTS = {"enabled": True, "webhook_url": "", "webhook_auth": "", "outbox_url": "",
            "ack_url": "", "outbox_auth": "", "allowlist": [], "allow_contacts": False,
            "max_sends_per_hour": 30, "max_body_len": 1000, "poll_seconds": 15,
            "tz_offset_hours": None}
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

def http(method, url, auth, payload=None, timeout=20):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if auth: req.add_header("Authorization", auth)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read(5_000_000)
    return json.loads(body) if body.strip() else None

def flush_queue(cfg, post=http):
    """POST queued messages in order; stop at first failure (kept on disk)."""
    if not os.path.exists(QUEUE) or not cfg["webhook_url"]: return 0
    with open(QUEUE) as f: items = [l for l in f if l.strip()]
    sent = 0
    for line in items:
        try:
            post("POST", cfg["webhook_url"], cfg["webhook_auth"], json.loads(line)); sent += 1
        except Exception as e:
            log("webhook_error", error=str(e)[:200]); break
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
    if key not in allowed and not (cfg["allow_contacts"] and key in {norm(n) for n in contacts_fn()}):
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

def poll_outbox(cfg, state, get=http, sender=send_sms, contacts_fn=contact_numbers):
    if not cfg["outbox_url"]: return
    items = get("GET", cfg["outbox_url"], cfg["outbox_auth"]) or []
    if not isinstance(items, list): return
    items = [it for it in items[:100] if isinstance(it, dict)]
    sent_ids = state.setdefault("sent_ids", {})
    state.setdefault("send_times", [])
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
                    status, err = "failed", str(e)
            else:
                status = "rejected"
            sent_ids[iid] = status; save_json(STATE, state)
            log("send", id=iid, to=it.get("to"), body=it.get("body"), status=status, error=err)
        if cfg["ack_url"]:
            try: get("POST", cfg["ack_url"], cfg["outbox_auth"], {"id": iid, "status": status, "error": err})
            except Exception as e: log("ack_error", id=iid, error=str(e)[:200])

def main():
    os.umask(0o077)  # state/log/queue contain message text: owner-only
    os.makedirs(BASE, mode=0o700, exist_ok=True)
    backoff = 0
    while True:
        cfg = load_config()
        if disabled(cfg):
            time.sleep(15); continue
        state = load_json(STATE, {})
        if "last_rowid" not in state:  # first run: start from now, don't replay history
            con = sqlite3.connect("file:%s?mode=ro" % CHATDB, uri=True)
            state["last_rowid"] = con.execute("SELECT IFNULL(MAX(ROWID),0) FROM message").fetchone()[0]
            con.close(); save_json(STATE, state); log("start", last_rowid=state["last_rowid"])
        ok = True
        try: poll_inbound(cfg, state)
        except Exception as e: log("read_error", error=str(e)[:300]); ok = False
        if flush_queue(cfg): ok = False
        try: poll_outbox(cfg, state)
        except Exception as e: log("outbox_error", error=str(e)[:300]); ok = False
        backoff = 0 if ok else min(300, max(15, backoff * 2))
        time.sleep(backoff or cfg["poll_seconds"])

if __name__ == "__main__":
    main()
