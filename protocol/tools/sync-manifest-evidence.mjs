#!/usr/bin/env node
// Rewrites protocol/v1/manifest.json evidence_ids/status from the derived inventory
// (checks/evidence.mjs). Only evidence lists and the derived status move; requirement
// ids, anchors and the documentary-only partition are never edited by hand.
// Usage: node protocol/tools/sync-manifest-evidence.mjs [--check]
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { buildEvidenceInventory } from "../checks/evidence.mjs";

const v1 = join(dirname(fileURLToPath(import.meta.url)), "..", "v1");
const path = join(v1, "manifest.json");
const text = readFileSync(path, "utf8");
const manifest = JSON.parse(text);
const derived = new Map(buildEvidenceInventory(v1).entries.map((e) => [e.id, e]));
for (const entry of manifest.entries) {
  const d = derived.get(entry.id);
  entry.evidence_ids = d.evidence_ids;
  entry.status = d.status;
}
const next = JSON.stringify(manifest, null, 2) + "\n";
if (process.argv.includes("--check")) {
  if (next !== text) {
    console.error("manifest.json evidence is stale; run protocol/tools/sync-manifest-evidence.mjs");
    process.exit(1);
  }
} else {
  writeFileSync(path, next);
}
