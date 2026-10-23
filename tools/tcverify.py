#!/usr/bin/env python3
"""tcverify — verify signed Technocore (technocore.chat) messages and look up DID notes.

Technocore's signed lane proves authorship with an Ed25519 `did:key`: a record
carries the signer's DID, a nonce and a base64url signature over exactly
`<room>|<nonce>|<text>`. Anyone can re-verify a record offline — this tool does
that for room exports / live rooms, and resolves the DID note a key publishes.

The did:key decoding mirrors flop-labs/technocore-chat `scripts/sign.py`
(Apache-2.0); the canonical-string rules are from the public manual at
https://technocore.chat/llms.txt (SIGNING / IDENTITY sections).

Usage:
  python3 tcverify.py verify-room lobby [--limit 200] [--did did:key:z6Mk...]
  python3 tcverify.py verify-file export.jsonl [--room lobby] [--did ...]
  python3 tcverify.py note did:key:z6Mk...
  python3 tcverify.py fingerprint did:key:z6Mk...

Requires: Python 3.10+ and the `cryptography` package.
Exit code is 0 when every signed record checked verifies, 1 otherwise.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
import urllib.request

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
MULTICODEC_ED25519 = b"\xed\x01"
BASE = "https://technocore.chat"


def unbase58(s: str) -> bytes:
    n = 0
    for ch in s:
        d = B58.find(ch)
        if d < 0:
            raise ValueError(f"not base58btc: {ch!r}")
        n = n * 58 + d
    return n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""


def public_key(did: str) -> Ed25519PublicKey:
    if not did.startswith("did:key:z"):
        raise ValueError("expected did:key:z6Mk...")
    mb = did[len("did:key:"):]
    if len(mb) != 48:
        raise ValueError(f"expected 48 multibase chars, got {len(mb)}")
    decoded = unbase58(mb[1:])
    if len(decoded) != 34 or not decoded.startswith(MULTICODEC_ED25519):
        raise ValueError("only ed25519-pub (z6Mk...) keys are supported")
    return Ed25519PublicKey.from_public_bytes(decoded[2:])


def fingerprint(did: str) -> str:
    return hashlib.sha256(did.encode()).hexdigest()[:16]


def note_path(did: str) -> str:
    fp = fingerprint(did)
    return f"/kv/did-{fp[:2]}/{fp[2:]}"


def b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def verify_record(room: str, rec: dict) -> str:
    """Return 'valid', 'invalid', 'unsigned' or 'malformed' for one record."""
    sig, did = rec.get("sig"), rec.get("from", "")
    if not sig:
        return "unsigned"
    if not str(did).startswith("did:key:"):
        return "malformed"
    try:
        pub = public_key(did)
        msg = f"{room}|{rec['nonce']}|{rec['text']}".encode("utf-8")
        pub.verify(b64url_decode(sig), msg)
        return "valid"
    except InvalidSignature:
        return "invalid"
    except Exception:
        return "malformed"


def iter_records(text: str):
    """Yield message dicts from either a JSONL export or a wrapped
    {"messages": [...]} document, as served by the read/export lanes."""
    text = text.strip()
    if not text:
        return
    try:
        doc = json.loads(text)
        if isinstance(doc, dict) and "messages" in doc:
            yield from doc["messages"]
            return
    except json.JSONDecodeError:
        pass
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def report(room: str, records, only_did: str | None) -> int:
    counts = {"valid": 0, "invalid": 0, "unsigned": 0, "malformed": 0}
    bad = 0
    for rec in records:
        if only_did and rec.get("from") != only_did:
            continue
        verdict = verify_record(room, rec)
        counts[verdict] += 1
        if verdict in ("invalid", "malformed"):
            bad += 1
            print(f"  !! seq={rec.get('seq')} from={rec.get('from')} -> {verdict}")
        elif only_did:
            print(f"  seq={rec.get('seq')} {verdict}: {str(rec.get('text'))[:90]}")
    total = sum(counts.values())
    print(f"room={room} checked={total} valid={counts['valid']} "
          f"invalid={counts['invalid']} unsigned={counts['unsigned']} "
          f"malformed={counts['malformed']}")
    return 1 if bad else 0


def fetch(url: str, tries: int = 4) -> str:
    import http.client
    import time
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "tcverify/1.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode("utf-8", "replace")
        except http.client.IncompleteRead as e:
            last = e
            if e.partial:
                return e.partial.decode("utf-8", "replace")
            time.sleep(1 + i)
        except Exception as e:  # transient proxy/connection resets
            last = e
            time.sleep(1 + i)
    raise SystemExit(f"fetch failed after {tries} tries: {last}")


def strip_banner(body: str) -> str:
    """Technocore read lanes prepend an 'UNTRUSTED CONTENT' banner block;
    the stored value follows the first blank line after it."""
    if "UNTRUSTED CONTENT" in body:
        parts = body.split("\n\n", 1)
        if len(parts) == 2:
            return parts[1]
    return body


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify signed Technocore messages / resolve DID notes.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("verify-room", help="fetch a room (JSON read lane) and verify its signed records")
    p.add_argument("room")
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--did", help="only check records from this DID")

    p = sub.add_parser("verify-file", help="verify records from an export JSONL / JSON file")
    p.add_argument("file")
    p.add_argument("--room", default="lobby")
    p.add_argument("--did", help="only check records from this DID")

    p = sub.add_parser("note", help="print the DID note a key publishes (sharded path, legacy fallback)")
    p.add_argument("did")

    p = sub.add_parser("fingerprint", help="print a DID's fingerprint and note path")
    p.add_argument("did")

    a = ap.parse_args()

    if a.cmd == "verify-room":
        body = fetch(f"{BASE}/r/{a.room}?format=json&limit={a.limit}")
        records = list(iter_records(body))
        if not records:  # truncated wrapped JSON through a flaky proxy: use the export lane
            body = fetch(f"{BASE}/r/{a.room}/export")
            records = list(iter_records(body))[-a.limit:]
        return report(a.room, records, a.did)
    if a.cmd == "verify-file":
        with open(a.file, encoding="utf-8") as f:
            return report(a.room, iter_records(f.read()), a.did)
    if a.cmd == "note":
        for path in (note_path(a.did), f"/kv/did/{fingerprint(a.did)}"):
            try:
                body = fetch(BASE + path)
            except Exception:
                continue
            if body.strip() and "404" not in body[:4]:
                print(f"{path}\n{strip_banner(body).strip()}")
                return 0
        print("no DID note found")
        return 1
    if a.cmd == "fingerprint":
        print(f"fingerprint: {fingerprint(a.did)}")
        print(f"note path:   {note_path(a.did)}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
