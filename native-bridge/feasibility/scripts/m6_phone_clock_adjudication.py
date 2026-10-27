"""Deterministic M6 phone-clock adjudication (A-02 evidence algorithm; stdlib only, read-only).

Input: the phone's ``otp23-transport-evidence`` JSON for an M6 run (two centrals on ONE phone). Everything is
computed on the phone's single ``elapsed_realtime_ms`` clock, so no desktop clock offset is involved.

Algorithm (per peer, events in ``seq`` order):
1. every ``notify_api`` event is one 512-byte-class fragment; consecutive groups of ``FRAGMENTS_PER_RECORD`` (9)
   form one 4096-byte record (8 x 512 + 36 bytes, checked);
2. each fragment's ``notify_callback`` is the event whose ``bound_op`` equals the API event's ``bound_op``;
3. a record's interval = [first fragment's API elapsed, last bound callback's elapsed];
4. two records of DIFFERENT peers overlap when the open intervals intersect; the overlap is the intersection length;
5. survivors = the later-disconnecting peer's records whose first API call is after the EARLIEST other peer's
   ``disconnected`` event, and the notification (fragment) count of those records.

This is not authentication: peers are SHA-256 prefixes of central addresses. It reproduces the numbers of the
manual A-02 adjudication and never rewrites the retained machine FAIL.

Usage: python3 scripts/m6_phone_clock_adjudication.py PHONE_TRANSPORT.json   (prints JSON)
"""

import hashlib
import json
import pathlib
import sys

FRAGMENTS_PER_RECORD = 9
RECORD_FRAGMENT_BYTES = [512] * 8 + [36]


def peer_records(peer):
    events = sorted(peer["events"], key=lambda e: e["seq"])
    callbacks = {}
    for event in events:
        if event["kind"] == "notify_callback":
            callbacks[event["bound_op"]] = event
    api = [e for e in events if e["kind"] == "notify_api"]
    if len(api) % FRAGMENTS_PER_RECORD:
        raise ValueError("notify_api count is not a multiple of %d" % FRAGMENTS_PER_RECORD)
    records = []
    for start in range(0, len(api), FRAGMENTS_PER_RECORD):
        group = api[start:start + FRAGMENTS_PER_RECORD]
        if [e["bytes"] for e in group] != RECORD_FRAGMENT_BYTES:
            raise ValueError("fragment sizes of record %d are not 8x512+36" % (start // FRAGMENTS_PER_RECORD))
        bound = [callbacks[e["bound_op"]] for e in group]
        records.append({"start": group[0]["elapsed_realtime_ms"],
                        "end": max(c["elapsed_realtime_ms"] for c in bound), "fragments": len(group)})
    disconnects = [e["elapsed_realtime_ms"] for e in events if e["kind"] == "disconnected"]
    return {"records": records, "disconnected": min(disconnects) if disconnects else None}


def adjudicate(document):
    peers = {p["peer_sha256_prefix"]: peer_records(p) for p in document["peers"]}
    if len(peers) != 2:
        raise ValueError("M6 needs exactly two peers")
    (name_a, a), (name_b, b) = sorted(peers.items())
    pairs, total = 0, 0
    for ra in a["records"]:
        for rb in b["records"]:
            overlap = min(ra["end"], rb["end"]) - max(ra["start"], rb["start"])
            if overlap > 0:
                pairs += 1
                total += overlap
    first = min((n for n in peers if peers[n]["disconnected"] is not None), key=lambda n: peers[n]["disconnected"])
    survivor = name_b if first == name_a else name_a
    cut = peers[first]["disconnected"]
    after = [r for r in peers[survivor]["records"] if r["start"] > cut]
    return {"run_id": document.get("run_id"),
            "records_per_peer": {n: len(p["records"]) for n, p in peers.items()},
            "overlapping_record_pairs": pairs, "summed_overlap_ms": total,
            "earliest_disconnect_peer": first, "survivor_peer": survivor,
            "survivor_records_after_disconnect": len(after),
            "survivor_notifications_after_disconnect": sum(r["fragments"] for r in after),
            "clock": "phone elapsed_realtime_ms (single clock)",
            "not": "no cryptographic peer identity, no protocol authentication, not OTP-24 GO"}


def main(argv=None):
    path = pathlib.Path((argv or sys.argv[1:])[0])
    data = path.read_bytes()
    result = adjudicate(json.loads(data))
    result["input_sha256"] = hashlib.sha256(data).hexdigest()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
