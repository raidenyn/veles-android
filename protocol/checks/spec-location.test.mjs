// The protocol node owns its own specification (split-android-and-granular-ci
// spec: component versioning). The normative specification, its API document
// and the review records live under protocol/docs/ so edits to them select the
// protocol checks that read them. No stub may remain at the old docs/protocol
// path, and the evidence inventory must resolve the specification from the new
// location only.
//
//   node --test protocol/checks/spec-location.test.mjs

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  cpSync,
  copyFileSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { buildEvidenceInventory } from "./evidence.mjs";

const checksDir = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(checksDir, "..", "..");
const v1Root = join(checksDir, "..", "v1");
const specPath = join(repoRoot, "protocol", "docs", "veles-v1.md");

test("the normative specification lives under protocol/docs with no stub at docs/protocol", () => {
  assert.ok(existsSync(specPath), "protocol/docs/veles-v1.md must exist");
  assert.equal(
    existsSync(join(repoRoot, "docs", "protocol")),
    false,
    "docs/protocol must not exist (no stub pointer left behind)",
  );
});

test("the evidence inventory resolves the specification from protocol/docs", () => {
  const report = buildEvidenceInventory(v1Root);
  assert.equal(report.entries.length, 156);
  assert.ok(
    report.entries.every((entry) => entry.document_anchor.startsWith("veles-v1.md#")),
    "every requirement anchor must come from the moved specification",
  );
  assert.ok(
    readFileSync(specPath, "utf8").includes('<a id="sec-'),
    "the resolved document must be the anchored normative specification",
  );
});

test("a detached tree resolves the specification only from protocol/docs", () => {
  const root = mkdtempSync(join(tmpdir(), "veles-spec-location-"));
  try {
    const v1 = join(root, "protocol", "v1");
    mkdirSync(v1, { recursive: true });
    copyFileSync(join(v1Root, "manifest.json"), join(v1, "manifest.json"));
    cpSync(join(v1Root, "fixtures"), join(v1, "fixtures"), { recursive: true });

    // A document left at the old layout must not be found.
    mkdirSync(join(root, "docs", "protocol"), { recursive: true });
    copyFileSync(specPath, join(root, "docs", "protocol", "veles-v1.md"));
    assert.throws(
      () => buildEvidenceInventory(v1),
      /ENOENT/,
      "the resolver must not fall back to docs/protocol",
    );

    // The same tree with the document at the new layout resolves.
    mkdirSync(join(root, "protocol", "docs"), { recursive: true });
    copyFileSync(specPath, join(root, "protocol", "docs", "veles-v1.md"));
    assert.equal(buildEvidenceInventory(v1).entries.length, 156);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
