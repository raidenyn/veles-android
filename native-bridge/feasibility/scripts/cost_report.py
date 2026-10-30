"""Dependency, build, binary and runtime cost: plain-Rust host vs Tauri variants (Windows).

Variants (all share the identical host library and the committed Cargo.lock):
  baseline        otp23-host                 default features (windows + serde only)
  tauri-linked    otp23-host                 --features tauri-linked: production-equivalent Tauri
                                             pin (=2.6.0, no features) compiled as a dependency but
                                             never started by the binary
  tauri-runtime   otp23-host-tauri           --features tauri-runtime: windowless Tauri app (wry,
                                             zero windows) that runs the same host

Usage:  python scripts/cost_report.py [--builds] [--out evidence/cost.json]
  --builds also does clean release builds in target-cost/<variant> (minutes for Tauri).
Writes the JSON, two SVG charts and per-variant CycloneDX SBOMs under evidence/.
"""

import argparse
import json
import os
import pathlib
import shutil
import statistics
import struct
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
TARGET = "x86_64-pc-windows-msvc"
VARIANTS = {
    "baseline": {"features": [], "bin": "otp23-host"},
    "tauri-linked": {"features": ["tauri-linked"], "bin": "otp23-host"},
    "tauri-runtime": {"features": ["tauri-runtime"], "bin": "otp23-host-tauri"},
}
CARGO = str(pathlib.Path.home() / ".cargo" / "bin" / "cargo.exe")
CYCLONEDX = str(pathlib.Path.home() / ".cargo" / "veles-otp23-tools" / "bin" / "cargo-cyclonedx.exe")
DUMPBIN_GLOB = r"C:\Program Files\Microsoft Visual Studio\18\Community\VC\Tools\MSVC\*\bin\Hostx64\x64\dumpbin.exe"
ORIGIN = "chrome-extension://gmekhgkipjkikmaadaajljinkgfielei/"


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT, **kw)


def feature_args(features):
    return ["--features", ",".join(features)] if features else []


def graph_metrics(features):
    """Reachable crates (normal + build edges) for the Windows target from `cargo metadata`."""
    meta = json.loads(run([CARGO, "metadata", "--format-version", "1", "--filter-platform", TARGET,
                           *feature_args(features)]).stdout)
    packages = {p["id"]: p for p in meta["packages"]}
    nodes = {n["id"]: n for n in meta["resolve"]["nodes"]}
    root = meta["resolve"]["root"]
    seen, stack = set(), [root]
    direct = []
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        for dep in nodes[current]["deps"]:
            kinds = {k["kind"] for k in dep["dep_kinds"]}
            if kinds <= {"dev"}:
                continue
            if current == root:
                direct.append(dep["pkg"])
            stack.append(dep["pkg"])
    seen.discard(root)
    crates = [packages[i] for i in seen]
    kinds = lambda p: {k for t in p["targets"] for k in t["kind"]}  # noqa: E731
    return {
        "direct_dependencies": len(set(direct)),
        "unique_crates_transitive": len(crates),
        "unique_crate_names": len({p["name"] for p in crates}),
        "proc_macro_crates": sum(1 for p in crates if "proc-macro" in kinds(p)),
        "build_script_crates": sum(1 for p in crates if "custom-build" in kinds(p)),
        "native_link_crates": sum(1 for p in crates if p.get("links")),
        "distinct_licenses": len({p.get("license") or "unspecified" for p in crates}),
        "crates_without_license_field": sum(1 for p in crates if not p.get("license")),
        "windows_family_crates": sorted({f"{p['name']} {p['version']}" for p in crates
                                         if p["name"].startswith(("windows", "webview2"))}),
        "tauri_family_crates": sorted({f"{p['name']} {p['version']}" for p in crates
                                       if p["name"].startswith(("tauri", "wry", "tao"))}),
    }


def dependents(exe):
    matches = sorted(pathlib.Path("C:/").glob(DUMPBIN_GLOB[3:]))
    if not matches:
        return None
    out = subprocess.run([str(matches[-1]), "/dependents", str(exe)], capture_output=True, text=True).stdout
    names = []
    capture = False
    for line in out.splitlines():
        line = line.strip()
        if line.lower().endswith(".dll"):
            names.append(line)
    return sorted(set(names), key=str.lower)


def frame(obj):
    data = json.dumps(obj).encode()
    return struct.pack("<I", len(data)) + data


def read_frame(stream):
    header = stream.read(4)
    if len(header) < 4:
        return None
    return json.loads(stream.read(struct.unpack("<I", header)[0]))


def process_stats(pid):
    ps = (f"Get-Process -Id {pid} | Select-Object WorkingSet64,PrivateMemorySize64,"
          "@{n='Threads';e={$_.Threads.Count}},HandleCount | ConvertTo-Json -Compress")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True).stdout
    return json.loads(out)


def runtime_probe(exe, runs=7):
    rows = []
    for _ in range(runs):
        started = time.perf_counter()
        proc = subprocess.Popen([str(exe), ORIGIN], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        proc.stdin.write(frame({"envelope_version": 1, "type": "hello", "request_id": "h", "api_versions": [1]}))
        proc.stdin.flush()
        assert read_frame(proc.stdout)["type"] == "ready"
        ready_ms = (time.perf_counter() - started) * 1000
        time.sleep(0.4)  # let background threads settle
        stats = process_stats(proc.pid)
        proc.stdin.write(frame({"envelope_version": 1, "api_version": 1, "type": "shutdown", "request_id": "s"}))
        proc.stdin.flush()
        assert read_frame(proc.stdout)["type"] == "shutdown_complete"
        shutdown_sent = time.perf_counter()
        proc.wait(timeout=30)
        rows.append({"ready_ms": ready_ms, "exit_after_shutdown_ms": (time.perf_counter() - shutdown_sent) * 1000,
                     "working_set_mb": stats["WorkingSet64"] / 2**20,
                     "private_mb": stats["PrivateMemorySize64"] / 2**20,
                     "threads": stats["Threads"], "handles": stats["HandleCount"]})
    med = lambda key: round(statistics.median(r[key] for r in rows), 1)  # noqa: E731
    return {key: med(key) for key in rows[0]} | {"runs": runs}


def build(variant, spec, clean):
    target_dir = ROOT / "target-cost" / variant
    if clean and target_dir.exists():
        shutil.rmtree(target_dir)
    env = dict(os.environ, CARGO_TARGET_DIR=str(target_dir))
    started = time.time()
    result = subprocess.run([CARGO, "build", "--release", "--bin", spec["bin"], *feature_args(spec["features"])],
                            cwd=ROOT, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-2000:])
    return round(time.time() - started, 1), target_dir / "release" / f"{spec['bin']}.exe"


def sbom(variant, spec, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"{variant}.cdx.json"
    cmd = [CYCLONEDX, "cyclonedx", "--manifest-path", str(ROOT / "Cargo.toml"), "--format", "json",
           "--target", TARGET, "--override-filename", f".sbom-{variant}"]
    if spec["features"]:
        cmd += ["--features", ",".join(spec["features"])]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    produced = ROOT / f".sbom-{variant}.json"
    if result.returncode != 0 or not produced.exists():
        return {"error": (result.stderr or result.stdout)[-400:]}
    document = json.loads(produced.read_text(encoding="utf-8"))
    produced.replace(out_dir / name)
    components = document.get("components", [])
    return {"file": f"sbom/{name}", "components": len(components),
            "sbom_bytes": (out_dir / name).stat().st_size}


def svg_bars(path, title, unit, rows):
    width, bar_h, gap, left = 760, 28, 14, 190
    peak = max(value for _, value in rows) or 1
    height = 70 + len(rows) * (bar_h + gap)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" font-family="Segoe UI,Arial" font-size="13">',
             f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
             f'<text x="12" y="26" font-size="16" font-weight="600">{title}</text>']
    colors = ["#2f6f4f", "#b58a1b", "#a23b3b"]
    for i, (label, value) in enumerate(rows):
        y = 50 + i * (bar_h + gap)
        w = (width - left - 120) * value / peak
        parts.append(f'<text x="12" y="{y + 19}">{label}</text>')
        parts.append(f'<rect x="{left}" y="{y}" width="{max(w, 1):.1f}" height="{bar_h}" fill="{colors[i % 3]}"/>')
        parts.append(f'<text x="{left + w + 8:.1f}" y="{y + 19}">{value:g} {unit}</text>')
    parts.append("</svg>")
    path.write_text("\n".join(parts), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--builds", action="store_true", help="clean release builds per variant (slow)")
    parser.add_argument("--out", default="evidence/cost.json")
    args = parser.parse_args()
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    report = {"target": TARGET, "rustc": run([CARGO, "--version"]).stdout.strip(), "variants": {}}
    for variant, spec in VARIANTS.items():
        print("==", variant, flush=True)
        entry = {"features": spec["features"], "binary": spec["bin"], "graph": graph_metrics(spec["features"])}
        entry["sbom"] = sbom(variant, spec, out.parent / "sbom")
        if args.builds:
            seconds, exe = build(variant, spec, clean=True)
            entry["clean_release_build_seconds"] = seconds
            entry["binary_bytes"] = exe.stat().st_size
            entry["binary_sha256"] = subprocess.run(
                ["powershell", "-NoProfile", "-Command", f"(Get-FileHash -Algorithm SHA256 '{exe}').Hash.ToLower()"],
                capture_output=True, text=True).stdout.strip()
            entry["imported_dlls"] = dependents(exe)
            if variant != "tauri-linked":
                entry["runtime"] = runtime_probe(exe)
        report["variants"][variant] = entry
        print(json.dumps({k: v for k, v in entry.items() if k != "graph"}, indent=1)[:600], flush=True)
    out.write_text(json.dumps(report, indent=2).replace(str(pathlib.Path.home()), "%USERPROFILE%"), encoding="utf-8")
    names = list(VARIANTS)
    svg_bars(out.parent / "cost-dependencies.svg", "Unique crates compiled for x86_64-pc-windows-msvc", "crates",
             [(n, report["variants"][n]["graph"]["unique_crates_transitive"]) for n in names])
    if args.builds:
        svg_bars(out.parent / "cost-binary-size.svg", "Release binary size (default profile)", "KiB",
                 [(n, round(report["variants"][n]["binary_bytes"] / 1024)) for n in names if n != "tauri-linked"])
        svg_bars(out.parent / "cost-build-time.svg", "Clean release build time", "s",
                 [(n, report["variants"][n]["clean_release_build_seconds"]) for n in names])
    print("wrote", out)


if __name__ == "__main__":
    sys.exit(main())
