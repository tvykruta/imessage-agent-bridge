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
        cfg = dict(self.cfg, webhook_url="https://x/h"); n = [0]
        def post(m, u, a, p):
            n[0] += 1
            if n[0] == 2: raise OSError("down")
        self.assertEqual(sb.flush_queue(cfg, post), 1); self.assertEqual(sb.flush_queue(cfg, lambda *a: None), 0)
    def test_no_shell(self):
        src = open(sb.__file__).read(); self.assertNotIn("shell=True", src); self.assertEqual(src.count("subprocess.run"), 1)
if __name__ == "__main__": unittest.main()
