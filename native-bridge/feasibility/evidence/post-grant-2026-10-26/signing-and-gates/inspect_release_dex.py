"""Search every classes*.dex of a release APK for debug-only identifiers (read-only; prints the evidence).
Usage: python3 -I inspect_release_dex.py APK EXPECTED_SHA256"""
import hashlib, sys, zipfile

NEEDLES = ["TransportTelemetry", "SyntheticTransportFixture", "scheduleSyntheticPush", "OTP23_POC",
           "otp23-transport-evidence", "otp23-unsolicited", "notify_api", "callback_after_close", "GATT_FAILURE"]
apk, want = sys.argv[1], sys.argv[2]
data = open(apk, "rb").read()
digest = hashlib.sha256(data).hexdigest()
print("apk_sha256", digest, "MATCHES_EXPECTED" if digest == want else "MISMATCH")
if digest != want:
    sys.exit(2)
with zipfile.ZipFile(apk) as z:
    dex = sorted(n for n in z.namelist() if n.startswith("classes") and n.endswith(".dex"))
    print("dex_files", " ".join(dex))
    blobs = {n: z.read(n) for n in dex}
total = 0
for needle in NEEDLES:
    hits = {n: b.count(needle.encode()) for n, b in blobs.items()}
    total += sum(hits.values())
    print("needle", needle, "hits", sum(hits.values()), dict(hits))
print("total_hits", total)
