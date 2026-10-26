"""Current (not historical) dependency-cost evidence for the Tauri-vs-plain-Rust decision.

Same reachability metric as the historical ``cost_report.py`` (normal + build edges from `cargo metadata`,
dev edges excluded) but portable: it measures whatever manifest and lockfile are checked out TODAY with
the pinned toolchain, for explicit target triples. It reads dependency graphs only; it never builds, so it
says nothing about binary size, build time or runtime behavior (those need the Windows/macOS builds and
stay separate rows in the requirements matrix).

Usage: python3 scripts/dependency_cost.py --out evidence/current-dependency-cost.json
"""

import argparse
import json
import pathlib
import subprocess
import sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
REPO = ROOT.parent.parent
TARGETS = ["x86_64-pc-windows-msvc", "aarch64-apple-darwin"]
VARIANTS = [
    ("feasibility-plain", ROOT, []),
    ("feasibility-tauri-linked", ROOT, ["tauri-linked"]),
    ("feasibility-tauri-runtime", ROOT, ["tauri-runtime"]),
    ("production-bridge-without-runtime", REPO / "native-bridge" / "src-tauri", []),
    ("production-bridge-tauri-runtime", REPO / "native-bridge" / "src-tauri", ["tauri-runtime"]),
]


def metrics(metadata):
    packages = {p["id"]: p for p in metadata["packages"]}
    nodes = {n["id"]: n for n in metadata["resolve"]["nodes"]}
    root = metadata["resolve"]["root"]
    seen, stack, direct = set(), [root], set()
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        for dep in nodes[current]["deps"]:
            if {k["kind"] for k in dep["dep_kinds"]} <= {"dev"}:
                continue
            if current == root:
                direct.add(dep["pkg"])
            stack.append(dep["pkg"])
    seen.discard(root)
    crates = [packages[i] for i in seen]
    kinds = lambda p: {k for t in p["targets"] for k in t["kind"]}  # noqa: E731
    return {
        "direct_dependencies": len(direct),
        "unique_crates_transitive": len(crates),
        "unique_crate_names": len({p["name"] for p in crates}),
        "proc_macro_crates": sum(1 for p in crates if "proc-macro" in kinds(p)),
        "build_script_crates": sum(1 for p in crates if "custom-build" in kinds(p)),
        "native_link_crates": sum(1 for p in crates if p.get("links")),
        "crates_without_license_field": sum(1 for p in crates if not p.get("license")),
        "tauri_family_crates": sorted({f"{p['name']} {p['version']}" for p in crates
                                       if p["name"].startswith(("tauri", "wry", "tao"))}),
    }


def cargo(args, cwd):
    return subprocess.run(["cargo", *args], capture_output=True, text=True, cwd=cwd, check=True).stdout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    report = {
        "kind": "current-dependency-graph",
        "measured_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cargo": cargo(["--version"], ROOT).strip(),
        "metric": "cargo metadata --locked --filter-platform TARGET; normal+build edges reachable from the root, dev excluded",
        "not_measured": ["binary size", "build time", "runtime behavior", "SBOM component licensing"],
        "variants": {},
    }
    for name, cwd, features in VARIANTS:
        report["variants"][name] = {"manifest_dir": str(cwd.relative_to(REPO)), "features": features, "targets": {}}
        for target in TARGETS:
            extra = ["--features", ",".join(features)] if features else []
            meta = json.loads(cargo(["metadata", "--locked", "--format-version", "1", "--filter-platform", target, *extra], cwd))
            report["variants"][name]["targets"][target] = metrics(meta)
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"dependency graph report -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
