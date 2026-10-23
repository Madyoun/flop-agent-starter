#!/usr/bin/env python3
"""Self-test for tcverify: a freshly signed record must verify, a tampered one
must not, and an unsigned record must be counted as unsigned.
Uses a public throwaway test key (seed 0x11 * 32) — never use it for anything real.
Run: python3 tests/selftest.py  (exit 0 = pass)"""
import importlib.util
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("tcverify", HERE.parent / "tools" / "tcverify.py")
tv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tv)

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

def did_of(key):
    raw = key.public_key().public_bytes_raw()
    n = int.from_bytes(b"\xed\x01" + raw, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = B58[r] + out
    return "did:key:z" + out

def sign(key, msg):
    import base64
    return base64.urlsafe_b64encode(key.sign(msg.encode())).decode().rstrip("=")

key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex("11" * 32))
did = did_of(key)
text = "tcverify self-test message"
nonce = int(time.time() * 1000)
sig = sign(key, f"lobby|{nonce}|{text}")

cases = [
    ({"seq": 1, "from": did, "text": text, "nonce": nonce, "sig": sig}, "valid"),
    ({"seq": 2, "from": did, "text": text + " TAMPERED", "nonce": nonce, "sig": sig}, "invalid"),
    ({"seq": 3, "from": "~anon", "text": "unsigned hello"}, "unsigned"),
]
ok = True
for rec, want in cases:
    got = tv.verify_record("lobby", rec)
    print(f"seq={rec['seq']}: want={want} got={got}")
    ok &= got == want
print("SELFTEST", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
