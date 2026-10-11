import os, json, hmac, re, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import redis
R = redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
TOKEN = os.environ["OUTBOX_TOKEN"]
if len(TOKEN) < 24: raise SystemExit("OUTBOX_TOKEN must be >= 24 chars")
TTL = int(os.environ.get("PENDING_TTL", 86400))
def mask(t): return "***" + re.sub(r"\D", "", str(t))[-4:]
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def out(self, code, obj):
        b = json.dumps(obj).encode(); self.send_response(code)
        self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def authed(self):
        ok = hmac.compare_digest(self.headers.get("Authorization", "").encode(), ("Bearer " + TOKEN).encode())
        if not ok: self.out(401, {"error": "unauthorized"})
        return ok
    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n < 0 or n > 20000: raise ValueError("too large")
        return json.loads(self.rfile.read(n) or b"{}")
    def do_GET(self):
        if self.path == "/healthz": return self.out(200, {"ok": True})
        if not self.authed(): return
        if self.path != "/outbox": return self.out(404, {"error": "not found"})
        items, now = [], time.time()
        for k, v in sorted(R.hgetall("outbox").items(), key=lambda kv: json.loads(kv[1])["ts"]):
            it = json.loads(v)
            if now - it["ts"] > TTL: R.hdel("outbox", k); print("expired", k); continue
            items.append({"id": it["id"], "to": it["to"], "body": it["body"]})
        self.out(200, items)
    def do_POST(self):
        if not self.authed(): return
        try: d = self.body()
        except Exception: return self.out(400, {"error": "bad json"})
        if self.path == "/enqueue":
            iid, to, b = str(d.get("id", "")), str(d.get("to", "")), d.get("body")
            if not iid or not to or not isinstance(b, str) or not b or len(b) > 1000:
                return self.out(400, {"error": "need id,to,body(<=1000)"})
            if R.exists("acked:" + iid): return self.out(409, {"error": "id already acked", "status": R.get("acked:" + iid)})
            R.hsetnx("outbox", iid, json.dumps({"id": iid, "to": to, "body": b, "ts": time.time()}))
            print("enqueue", iid, mask(to)); return self.out(200, {"queued": iid})
        if self.path == "/ack":
            iid, st = str(d.get("id", "")), str(d.get("status", ""))
            if not iid or st not in ("sent", "failed", "rejected"): return self.out(400, {"error": "need id,status"})
            R.hdel("outbox", iid); R.set("acked:" + iid, json.dumps({"status": st, "error": d.get("error")}), ex=7 * 86400)
            print("ack", iid, st); return self.out(200, {"acked": iid})
        if self.path == "/status":
            iid = str(d.get("id", "")); a = R.get("acked:" + iid)
            return self.out(200, {"id": iid, "pending": R.hexists("outbox", iid), "ack": json.loads(a) if a else None})
        self.out(404, {"error": "not found"})
ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", 10000))), H).serve_forever()
