import os, sys, json, sqlite3, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import smsbridge as sb

def ab(s):
    b = s.encode()
    ln = bytes([len(b)]) if len(b) < 0x80 else b"\x81" + len(b).to_bytes(2, "little")
    return b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString\x00\x84\x84\x08NSObject\x00\x85\x92\x84\x84\x84\x08NSString\x01\x94\x84\x01\x2b" + ln + b + b"\x86\x84\x02iI\x01"

class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); sb.LOG = os.path.join(self.d, "log"); sb.STATE = os.path.join(self.d, "st"); sb.QUEUE = os.path.join(self.d, "q")
        self.cfg = dict(sb.DEFAULTS, allowlist=["+1 (555) 555-0100"], outbox_url="https://x/out", ack_url="https://x/ack")
    def test_ab_short(self): self.assertEqual(sb.decode_attributed_body(ab("Send me the plans")), "Send me the plans")
    def test_ab_long_unicode(self):
        s = "é❤️ " * 60; self.assertEqual(sb.decode_attributed_body(ab(s)), s)
    def test_ab_none(self): self.assertIsNone(sb.decode_attributed_body(None))
    def test_time(self): self.assertTrue(sb.apple_time_to_iso(0, -5).startswith("2000-12-31T19:00:00-05:00"))
    def test_read_db(self):
        p = os.path.join(self.d, "chat.db"); c = sqlite3.connect(p)
        c.executescript("create table message(ROWID integer primary key,date int,handle_id int,is_from_me int,text text,attributedBody blob);create table handle(ROWID integer primary key,id text);create table chat(ROWID integer primary key,guid text);create table chat_message_join(chat_id int,message_id int);insert into handle values(1,'+15555550101');insert into chat values(1,'SMS;-;+15555550101');")
        c.execute("insert into message values(5,7.6e17,1,0,NULL,?)", (ab("hi there"),)); c.execute("insert into message values(6,7.6e17,1,1,'ok',NULL)")
        c.execute("insert into chat_message_join values(1,5)"); c.commit(); c.close()
        m = sb.read_new(p, 4, -5); self.assertEqual([x["text"] for x in m], ["hi there", "ok"]); self.assertEqual(m[0]["chat_guid"], "SMS;-;+15555550101")
        self.assertEqual(sb.read_new(p, 6), [])
    def test_guardrails(self):
        st = {}; ok = lambda: set()
        self.assertIsNone(sb.check_send({"to": "5555550100", "body": "hi"}, self.cfg, st, ok))
        self.assertEqual(sb.check_send({"to": "5555550199", "body": "hi"}, self.cfg, st, ok), "not_allowlisted")
        self.assertEqual(sb.check_send({"to": "5555550199", "body": "hi"}, self.cfg, st, lambda: {"5555550199"}), "not_allowlisted")
        self.assertIsNone(sb.check_send({"to": "5555550199", "body": "hi"}, dict(self.cfg, allow_contacts=True), st, lambda: {"5555550199"}))
        self.assertEqual(sb.check_send({"to": "+44 5555550100", "body": "hi"}, self.cfg, st, ok), "not_allowlisted")
        self.assertEqual(sb.check_send({"to": "-e do shell script 5555550100", "body": "hi"}, self.cfg, st, ok), "bad_recipient")
        self.assertEqual(sb.check_send({"to": "5555550100", "body": "x" * 1001}, self.cfg, st, ok), "body_too_long")
        st["send_times"] = [__import__("time").time()] * 30
        self.assertEqual(sb.check_send({"to": "5555550100", "body": "hi"}, self.cfg, st, ok), "rate_limited")
    def test_kill(self):
        k = os.path.join(self.d, "DISABLED"); self.assertFalse(sb.disabled(self.cfg, k))
        self.assertTrue(sb.disabled(dict(self.cfg, enabled=False), k)); open(k, "w").close(); self.assertTrue(sb.disabled(self.cfg, k))
    def test_outbox_idempotent(self):
        calls, acks, st = [], [], {}
        items = [{"id": "a", "to": "+15555550100", "body": "hi"}, {"id": "b", "to": "5555550199", "body": "x"}]
        def get(m, u, a, p=None):
            if m == "GET": return items
            acks.append(p)
        for _ in range(2): sb.poll_outbox(self.cfg, st, get, lambda t, b: calls.append(t), lambda: set())
        self.assertEqual(calls, ["+15555550100"]); self.assertEqual([a["status"] for a in acks], ["sent", "rejected"] * 2)
    def test_queue_retry(self):
        with open(sb.QUEUE, "w") as f: f.write('{"rowid":1}\n{"rowid":2}\n')
        cfg = dict(self.cfg, webhook_url="https://hooks.agent.test/h"); n = [0]; keys = []
        def post(m, u, a, p, headers=None):
            n[0] += 1; keys.append(headers["Idempotency-Key"])
            if n[0] == 2: raise OSError("down")
        self.assertEqual(sb.flush_queue(cfg, post), 1); self.assertEqual(sb.flush_queue(cfg, lambda *a, **k: None), 0)
        self.assertEqual(keys, ["imab-1", "imab-2"])  # same key on retry -> receiver can dedupe
    def test_no_shell(self):
        with open(sb.__file__) as f: src = f.read()
        self.assertNotIn("shell=True", src); self.assertEqual(src.count("subprocess.run"), 1)
    def test_email_domain_rejects_leading_hyphen(self):
        self.assertIsNone(sb.EMAIL_RE.match("a@-example.com")); self.assertIsNotNone(sb.EMAIL_RE.match("a@mail.example.com"))

class Ntfy(unittest.TestCase):
    def setUp(self):
        import base64, nacl.utils
        self.d = tempfile.mkdtemp(); sb.LOG = os.path.join(self.d, "log"); sb.STATE = os.path.join(self.d, "st")
        self.key = base64.urlsafe_b64encode(nacl.utils.random(32)).decode()
        self.cfg = dict(sb.DEFAULTS, allowlist=["+15555550100"], ntfy_topic="imab-test", outbox_key=self.key)
        self.sent, self.posts, self.st = [], [], {}
    def run_poll(self, msgs, cfg=None, now=None):
        def fetch(m, u, data=None):
            if m == "POST": self.posts.append((u, data)); return ""
            self.assertIn("/imab-test/json?poll=1&since=", u)
            return "\n".join(json.dumps({"id": "n%d" % i, "event": "message", "message": t}) for i, t in enumerate(msgs))
        sb.poll_ntfy(cfg or self.cfg, self.st, fetch, lambda t, b: self.sent.append((t, b)), lambda: set(), now)
    def enc(self, **kw):
        it = dict(id="m1", to="5555550100", body="hi"); it.update(kw); return sb.encrypt_item(self.key, it)
    def test_good_send(self):
        self.run_poll([self.enc()]); self.assertEqual(self.sent, [("+15555550100", "hi")]); self.assertEqual(self.st["ntfy_since"], "n0")
    def test_bad_key_and_tamper(self):
        import base64, nacl.utils
        other = sb.encrypt_item(base64.urlsafe_b64encode(nacl.utils.random(32)).decode(), {"id": "x", "to": "5555550100", "body": "hi"})
        raw = bytearray(base64.b64decode(self.enc())); raw[-1] ^= 1
        self.run_poll([other, base64.b64encode(bytes(raw)).decode(), "not base64!", json.dumps({"id": "p", "to": "5555550100", "body": "plain"})])
        self.assertEqual(self.sent, [])
    def test_replay(self):
        m = self.enc(); self.run_poll([m, m]); self.st.pop("ntfy_since"); self.run_poll([m]); self.assertEqual(len(self.sent), 1)
    def test_stale_and_future(self):
        import time
        self.run_poll([self.enc(id="a", ts=time.time() - 700), self.enc(id="b", ts=time.time() + 300)]); self.assertEqual(self.sent, [])
    def test_guardrails_still_apply(self):
        self.run_poll([self.enc(to="5555550199")]); self.assertEqual(self.sent, [])
    def test_bad_cap(self):
        self.run_poll(["junk"] * 50 + [self.enc()])
        with open(sb.LOG) as f: self.assertEqual(sum("ntfy_reject" in l for l in f), sb.MAX_BAD_PER_POLL)
    def test_long_body(self):
        cfg = dict(self.cfg, max_body_len=4000)
        self.run_poll([self.enc(id="a", body="x" * 1000), self.enc(id="b", body="y" * 4001)], cfg)
        self.assertEqual([len(b) for _, b in self.sent], [1000])
        self.assertLessEqual(len(self.enc(body="x" * 1000)), 4096)
    def test_acks(self):
        self.run_poll([self.enc()], dict(self.cfg, ntfy_acks=True)); self.assertTrue(self.posts[0][0].endswith("/imab-test-acks"))
    def test_transport_select(self):
        self.assertEqual(sb.transport(dict(sb.DEFAULTS)), "ntfy")
        self.assertEqual(sb.transport(dict(sb.DEFAULTS, outbox_url="https://x/out")), "http")
        self.assertEqual(sb.transport(dict(sb.DEFAULTS, outbox_url="https://x/out", ntfy_topic="t")), "ntfy")
        self.assertEqual(sb.transport(dict(sb.DEFAULTS, outbox_transport="http", ntfy_topic="t")), "http")
    def test_reference_sender_compatible(self):
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))); import send, urllib.request
        captured = []
        class R:
            def __enter__(s): return s
            def __exit__(s, *a): pass
            def read(s): return b""
        orig = urllib.request.urlopen; urllib.request.urlopen = lambda req, timeout: captured.append(req) or R()
        try: send.send("5555550100", "from send.py", "imab-test", self.key)
        finally: urllib.request.urlopen = orig
        self.run_poll([captured[0].data.decode()]); self.assertEqual(self.sent, [("+15555550100", "from send.py")])

class Setup(unittest.TestCase):
    """Install problems reported by a real user: placeholders, empty URL, empty allowlist, certs, zsh."""
    def setUp(self):
        self.d = tempfile.mkdtemp(); sb.LOG = os.path.join(self.d, "log"); sb.STATE = os.path.join(self.d, "st")
        self.good = dict(sb.DEFAULTS, webhook_url="https://hooks.agent.test/in", webhook_auth="Bearer abc123",
                         allowlist=["+15555550100"], ntfy_topic="imab-xyz",
                         outbox_key=__import__("base64").urlsafe_b64encode(b"k" * 32).decode())
    def test_good_config_has_no_problems(self):
        self.assertEqual(sb.config_problems(self.good), [])
    def test_placeholder_key_rejected(self):
        self.assertTrue(sb.config_problems(dict(self.good, webhook_auth="Bearer PASTE_KEY_HERE")))
        self.assertTrue(sb.config_problems(dict(self.good, webhook_auth="Bearer YOUR_WEBHOOK_KEY")))
        for k in ["PASTE_KEY_HERE", "", "two words", "<key>"]:
            with self.assertRaises(ValueError): sb.apply_answers(self.good, key=k)
        self.assertEqual(sb.apply_answers(self.good, key="Bearer s3cr3t")["webhook_auth"], "Bearer s3cr3t")
    def test_bad_webhook_url_is_plain_english(self):
        for u in ["", "http://hooks.agent.test/in", "hooks.agent.test", "https://your-agent.example.com/webhook", "https://x"]:
            msg = sb.check_webhook(dict(self.good, webhook_url=u)); self.assertTrue(msg, u); self.assertIn("webhook_url", msg)
        self.assertIsNone(sb.check_webhook(self.good))
        self.assertIn("server name could not be found", sb.explain(OSError("nodename nor servname provided")))
        self.assertIn("certifi", sb.explain(OSError("[SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer")))
    def test_empty_allowlist_flagged_and_parsed(self):
        self.assertTrue(any("allowlist" in p for p in sb.config_problems(dict(self.good, allowlist=[]))))
        self.assertEqual(sb.apply_answers(self.good, allow="(555) 555-0100, +1 555 555 0101")["allowlist"], ["+15555550100", "+15555550101"])
        with self.assertRaises(ValueError): sb.apply_answers(self.good, allow="call mom")
    def test_allow_all(self):
        st = {}; c = dict(self.good, allow_all=True, allowlist=[])
        self.assertIsNone(sb.check_send({"to": "5555550199", "body": "hi"}, c, st, lambda: set()))
        self.assertEqual(sb.config_problems(c), [])
        self.assertEqual(sb.check_send({"to": "-e x", "body": "hi"}, c, st, lambda: set()), "bad_recipient")
    def test_setup_writes_private_config_without_placeholders(self):
        import builtins, getpass
        p = os.path.join(self.d, "config.json"); sb.write_config(dict(self.good, webhook_url="", webhook_auth="", allowlist=[]), p)
        answers = iter(["https://hooks.agent.test/in", "+15555550100"])
        oi, og = builtins.input, getpass.getpass
        builtins.input = lambda *a: next(answers); getpass.getpass = lambda *a: "realkey123"
        try: sb.setup(["--no-test"], p)
        finally: builtins.input, getpass.getpass = oi, og
        c = json.load(open(p)); self.assertEqual(c["webhook_auth"], "Bearer realkey123"); self.assertEqual(c["allowlist"], ["+15555550100"])
        self.assertEqual(os.stat(p).st_mode & 0o777, 0o600); self.assertEqual(sb.config_problems(c), [])
    def test_ssl_context_uses_certifi(self):
        self.assertIsInstance(sb.ssl_context(), __import__("ssl").SSLContext)
    def test_doctor_reports(self):
        lines = []
        n = sb.doctor(dict(self.good, webhook_auth="Bearer PASTE_KEY_HERE"), lines.append, os.path.join(self.d, "none"),
                      fetch=lambda *a, **k: "", post=lambda *a, **k: None)
        self.assertGreaterEqual(n, 2); self.assertTrue(any("FIX" in l and "config" in l for l in lines))
    def test_reject_logs_clock(self):
        import base64, time
        key = self.good["outbox_key"]; box = sb._box(key)
        msg = sb.encrypt_item(key, {"id": "f", "to": "5555550100", "body": "hi", "ts": time.time() + 3600})
        sb.poll_ntfy(self.good, {}, lambda m, u, data=None: json.dumps({"id": "n", "event": "message", "message": msg, "time": 1}),
                     lambda t, b: None, lambda: set())
        e = [json.loads(l) for l in open(sb.LOG) if "ntfy_reject" in l][0]
        self.assertEqual(e["reason"], "future"); self.assertIn("mac_time", e); self.assertGreater(e["note_ts"], e["mac_time"]); self.assertTrue(e["hint"])
    def test_installer_has_no_zsh_only_or_placeholder_traps(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        src = open(os.path.join(root, "install.sh")).read() + open(os.path.join(root, "config.example.json")).read()
        self.assertNotIn("read -p", src); self.assertNotIn("PASTE_KEY_HERE", src); self.assertNotIn("YOUR_WEBHOOK_KEY", src)

if __name__ == "__main__": unittest.main()
