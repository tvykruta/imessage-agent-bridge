#!/usr/bin/env python3
"""Reference sender for agents: encrypt one text and publish it to the bridge's ntfy topic.

  pip install pynacl
  NTFY_TOPIC=imab-... OUTBOX_KEY=... python3 send.py +15555550100 "On my way"

Env: NTFY_TOPIC, OUTBOX_KEY (from install.sh), optional NTFY_SERVER (default https://ntfy.sh).
The Mac still decides: allowlist, rate limit, length cap, kill switch."""
import base64, json, os, sys, time, uuid, urllib.request
from nacl.secret import SecretBox
from nacl.utils import random

def send(to, body, topic, key_b64, server="https://ntfy.sh", msg_id=None):
    key = base64.urlsafe_b64decode(key_b64 + "=" * (-len(key_b64) % 4))
    item = {"id": msg_id or uuid.uuid4().hex, "to": to, "body": body, "ts": int(time.time())}
    data = base64.b64encode(bytes(SecretBox(key).encrypt(json.dumps(item).encode(), random(24))))
    if len(data) > 4096:  # ntfy turns bigger messages into attachments, which the bridge ignores
        raise ValueError("message too long for ntfy (%d bytes encoded, max 4096)" % len(data))
    req = urllib.request.Request("%s/%s" % (server.rstrip("/"), topic), data=data, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r: r.read()
    return item["id"]

if __name__ == "__main__":
    if len(sys.argv) != 3: sys.exit(__doc__)
    print(send(sys.argv[1], sys.argv[2], os.environ["NTFY_TOPIC"], os.environ["OUTBOX_KEY"],
               os.environ.get("NTFY_SERVER", "https://ntfy.sh")))
