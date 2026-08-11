# Extension-tab Web Bluetooth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Chrome action popup that cannot display a Web Bluetooth chooser with an action-launched extension tab that owns the existing Bluetooth session.

**Architecture:** A dependency-free launcher module creates or focuses one `popup.html` extension tab, and a minimal MV3 service worker invokes it from `chrome.action.onClicked`. The existing page remains the sole owner of Bluetooth, GATT, authentication, OTP, and clipboard state; only its launch context and responsive sizing change.

**Tech Stack:** Manifest V3, JavaScript ES modules, Chrome `action`, `tabs`, and `windows` APIs, browser Web Bluetooth, Web Crypto, Async Clipboard, and Node's built-in test runner without npm dependencies.

## Global Constraints

- Keep the extension under `spikes/web-bluetooth-popup/`; do not add npm, TypeScript, a bundler, an offscreen document, or a side panel.
- Do not add a `"bluetooth"` manifest permission; Chrome's MV3 permission list does not define one.
- Keep `popup.mjs` as the sole owner of every `BluetoothDevice`, GATT characteristic, protocol queue, heartbeat, OTP, and clipboard operation.
- The service worker may only launch or focus the connector tab; it must not own connection or application state.
- Repeated action clicks must focus the existing connector tab so active multi-phone sessions are not replaced by duplicate tabs.
- Closing the connector tab must destroy all live session state; reopening starts empty and calls `requestDevice()` again.
- Preserve all synthetic-only data and the public test key; do not connect this spike to real notification or production OTP data.
- Do not claim physical Bluetooth success from automated tests. Windows, macOS, and Android hardware validation remains required.
- Do not modify or remove `.tmp-exec/`; it is an unrelated untracked worktree artifact.
- The design in `docs/superpowers/specs/2026-08-11-extension-tab-web-bluetooth-design.md` supersedes the popup-only constraints in the original spike spec and implementation plan.

## File Map

- Create `spikes/web-bluetooth-popup/launcher.mjs`: pure create-or-focus connector-tab function with an injected Chrome API.
- Create `spikes/web-bluetooth-popup/launcher.test.mjs`: dependency-free launcher and manifest regression tests.
- Create `spikes/web-bluetooth-popup/service-worker.mjs`: register `chrome.action.onClicked` and report launcher failures.
- Modify `spikes/web-bluetooth-popup/manifest.json`: remove `default_popup`, retain only `clipboardWrite`, and register the module service worker.
- Modify `spikes/web-bluetooth-popup/popup.css`: replace the fixed 420 px popup width with a bounded responsive tab layout.
- Modify `spikes/web-bluetooth-popup/README.md`: change installation, DevTools, lifecycle, and matrix steps from popup to connector tab.
- Modify `docs/spikes/2026-08-11-popup-web-bluetooth-validation.md`: record the action-popup platform finding and change expected cases to tab ownership/lifecycle.
- Modify `docs/superpowers/specs/2026-08-11-popup-web-bluetooth-spike-design.md`: add a supersession notice pointing at the approved extension-tab design.

---

### Task 1: Create and focus the connector tab

**Files:**
- Create: `spikes/web-bluetooth-popup/launcher.test.mjs`
- Create: `spikes/web-bluetooth-popup/launcher.mjs`
- Create: `spikes/web-bluetooth-popup/service-worker.mjs`

**Interfaces:**
- Consumes: injected Chrome API object with `runtime.getURL(path)`, `tabs.query(queryInfo)`, `tabs.create(createProperties)`, `tabs.update(tabId, updateProperties)`, and `windows.update(windowId, updateInfo)`.
- Produces: `openConnector(chromeApi): Promise<void>` from `launcher.mjs`.
- Produces: one `chrome.action.onClicked` listener from `service-worker.mjs`.

- [ ] **Step 1: Write the failing create-tab test**

Create `spikes/web-bluetooth-popup/launcher.test.mjs`:

```js
import test from "node:test";
import assert from "node:assert/strict";
import { openConnector } from "./launcher.mjs";

test("creates the connector tab when none exists", async () => {
  const calls = [];
  const chromeApi = {
    runtime: {
      getURL: (path) => `chrome-extension://test/${path}`,
    },
    tabs: {
      query: async (query) => {
        calls.push(["query", query]);
        return [];
      },
      create: async (properties) => {
        calls.push(["create", properties]);
      },
      update: async () => assert.fail("must not update a missing tab"),
    },
    windows: {
      update: async () => assert.fail("must not focus a missing tab"),
    },
  };

  await openConnector(chromeApi);

  assert.deepEqual(calls, [
    ["query", { url: "chrome-extension://test/popup.html" }],
    ["create", { url: "chrome-extension://test/popup.html" }],
  ]);
});
```

- [ ] **Step 2: Run the test and verify the expected failure**

Run:

```bash
node --test spikes/web-bluetooth-popup/launcher.test.mjs
```

Expected: FAIL with `ERR_MODULE_NOT_FOUND` for `launcher.mjs`.

- [ ] **Step 3: Implement the minimal create-tab path**

Create `spikes/web-bluetooth-popup/launcher.mjs`:

```js
export async function openConnector(chromeApi) {
  const url = chromeApi.runtime.getURL("popup.html");
  const [existing] = await chromeApi.tabs.query({ url });
  if (existing) return;
  await chromeApi.tabs.create({ url });
}
```

- [ ] **Step 4: Run the test and verify it passes**

Run:

```bash
node --test spikes/web-bluetooth-popup/launcher.test.mjs
```

Expected: PASS, 1 test.

- [ ] **Step 5: Write the failing focus-existing-tab test**

Append to `spikes/web-bluetooth-popup/launcher.test.mjs`:

```js
test("activates and focuses an existing connector tab", async () => {
  const calls = [];
  const chromeApi = {
    runtime: {
      getURL: (path) => `chrome-extension://test/${path}`,
    },
    tabs: {
      query: async (query) => {
        calls.push(["query", query]);
        return [{ id: 17, windowId: 23 }];
      },
      create: async () => assert.fail("must not create a duplicate tab"),
      update: async (tabId, properties) => {
        calls.push(["update-tab", tabId, properties]);
      },
    },
    windows: {
      update: async (windowId, properties) => {
        calls.push(["update-window", windowId, properties]);
      },
    },
  };

  await openConnector(chromeApi);

  assert.deepEqual(calls, [
    ["query", { url: "chrome-extension://test/popup.html" }],
    ["update-window", 23, { focused: true }],
    ["update-tab", 17, { active: true }],
  ]);
});
```

- [ ] **Step 6: Run the test and verify the expected failure**

Run:

```bash
node --test spikes/web-bluetooth-popup/launcher.test.mjs
```

Expected: FAIL because `openConnector()` returns without focusing or activating the existing tab.

- [ ] **Step 7: Implement focus-existing-tab behavior**

Replace `openConnector()` in `spikes/web-bluetooth-popup/launcher.mjs` with:

```js
export async function openConnector(chromeApi) {
  const url = chromeApi.runtime.getURL("popup.html");
  const [existing] = await chromeApi.tabs.query({ url });
  if (existing) {
    await chromeApi.windows.update(existing.windowId, { focused: true });
    await chromeApi.tabs.update(existing.id, { active: true });
    return;
  }
  await chromeApi.tabs.create({ url });
}
```

- [ ] **Step 8: Add the action service worker**

Create `spikes/web-bluetooth-popup/service-worker.mjs`:

```js
import { openConnector } from "./launcher.mjs";

chrome.action.onClicked.addListener(() => {
  openConnector(chrome).catch((error) => {
    console.error("Failed to open connector tab", error);
  });
});
```

- [ ] **Step 9: Verify launcher tests and syntax**

Run:

```bash
node --test spikes/web-bluetooth-popup/launcher.test.mjs
node --check spikes/web-bluetooth-popup/launcher.mjs
node --check spikes/web-bluetooth-popup/service-worker.mjs
```

Expected: 2 tests pass and both syntax checks exit 0.

- [ ] **Step 10: Commit the launcher**

```bash
git add spikes/web-bluetooth-popup/launcher.mjs spikes/web-bluetooth-popup/launcher.test.mjs spikes/web-bluetooth-popup/service-worker.mjs
git commit -m "feat: launch Bluetooth connector in extension tab"
```

---

### Task 2: Register the tab launcher and make the connector responsive

**Files:**
- Modify: `spikes/web-bluetooth-popup/launcher.test.mjs`
- Modify: `spikes/web-bluetooth-popup/manifest.json`
- Modify: `spikes/web-bluetooth-popup/popup.css:6-11`

**Interfaces:**
- Consumes: `service-worker.mjs` and `openConnector(chromeApi)` from Task 1.
- Produces: an MV3 action with no `default_popup`, a module background service worker, and a connector page bounded to 720 px in wide tabs.

- [ ] **Step 1: Add failing manifest and CSS regression tests**

Add this import to `spikes/web-bluetooth-popup/launcher.test.mjs`:

```js
import { readFile } from "node:fs/promises";
```

Append these tests:

```js
test("manifest launches the connector through a module service worker", async () => {
  const manifest = JSON.parse(
    await readFile(new URL("./manifest.json", import.meta.url), "utf8"),
  );
  assert.deepEqual(manifest.permissions, ["clipboardWrite"]);
  assert.deepEqual(manifest.background, {
    service_worker: "service-worker.mjs",
    type: "module",
  });
  assert.equal(manifest.action.default_popup, undefined);
});

test("connector layout is responsive instead of popup-width fixed", async () => {
  const css = await readFile(new URL("./popup.css", import.meta.url), "utf8");
  assert.doesNotMatch(css, /width:\s*420px/);
  assert.match(css, /max-width:\s*720px/);
});
```

- [ ] **Step 2: Run the tests and verify both new tests fail**

Run:

```bash
node --test spikes/web-bluetooth-popup/launcher.test.mjs
```

Expected: 2 existing tests pass; manifest test fails because `background` is absent and `default_popup` exists; CSS test fails on `width: 420px` and missing `max-width: 720px`.

- [ ] **Step 3: Update the MV3 manifest**

Replace `spikes/web-bluetooth-popup/manifest.json` with:

```json
{
  "manifest_version": 3,
  "name": "Veles Web Bluetooth Spike",
  "version": "0.1.0",
  "description": "Synthetic-only tab-owned Web Bluetooth feasibility harness.",
  "permissions": ["clipboardWrite"],
  "background": {
    "service_worker": "service-worker.mjs",
    "type": "module"
  },
  "action": {
    "default_title": "Veles BLE Spike"
  }
}
```

This replacement intentionally removes the uncommitted, invalid `"bluetooth"` permission currently present in the worktree.

- [ ] **Step 4: Update the connector layout**

Replace the `body` rule at `spikes/web-bluetooth-popup/popup.css:6-11` with:

```css
body {
  box-sizing: border-box;
  margin: 0 auto;
  max-width: 720px;
  padding: 12px;
}
```

- [ ] **Step 5: Run focused tests and manifest validation**

Run:

```bash
node --test spikes/web-bluetooth-popup/launcher.test.mjs
node -e 'const fs=require("fs"); const m=JSON.parse(fs.readFileSync("spikes/web-bluetooth-popup/manifest.json","utf8")); if(m.manifest_version!==3||m.action.default_popup||m.permissions.includes("bluetooth")||m.background.service_worker!=="service-worker.mjs"||m.background.type!=="module") process.exit(1)'
```

Expected: 4 tests pass and manifest validation exits 0.

- [ ] **Step 6: Run all extension tests and syntax checks**

Run:

```bash
node --test spikes/web-bluetooth-popup/protocol.test.mjs spikes/web-bluetooth-popup/launcher.test.mjs
node --check spikes/web-bluetooth-popup/protocol.mjs
node --check spikes/web-bluetooth-popup/popup.mjs
node --check spikes/web-bluetooth-popup/launcher.mjs
node --check spikes/web-bluetooth-popup/service-worker.mjs
```

Expected: 9 tests pass and every syntax check exits 0.

- [ ] **Step 7: Commit manifest and layout changes**

```bash
git add spikes/web-bluetooth-popup/launcher.test.mjs spikes/web-bluetooth-popup/manifest.json spikes/web-bluetooth-popup/popup.css
git commit -m "fix: host Bluetooth connector in active tab"
```

---

### Task 3: Update the physical-validation handoff

**Files:**
- Modify: `spikes/web-bluetooth-popup/README.md:129-452`
- Modify: `docs/spikes/2026-08-11-popup-web-bluetooth-validation.md:1-124`
- Modify: `docs/superpowers/specs/2026-08-11-popup-web-bluetooth-spike-design.md:1-5`

**Interfaces:**
- Consumes: action-launched connector-tab behavior from Tasks 1 and 2.
- Produces: physical test instructions and expected results that accurately validate tab ownership and tab closure.

- [ ] **Step 1: Add a supersession notice to the original design**

Immediately below the title in `docs/superpowers/specs/2026-08-11-popup-web-bluetooth-spike-design.md`, add:

```markdown
> **Superseded launch context:** Physical testing showed that desktop Chrome
> rejects `requestDevice()` from an MV3 action popup before displaying a
> chooser. The approved replacement launch context is specified in
> `2026-08-11-extension-tab-web-bluetooth-design.md`; the Android protocol and
> synthetic-data constraints below remain applicable.
```

- [ ] **Step 2: Update extension installation and troubleshooting instructions**

In `spikes/web-bluetooth-popup/README.md`:

- Change the pinned-action instruction to say clicking the action opens or focuses the connector tab.
- Change every instruction to open, reopen, inspect, or close the popup to the connector tab equivalent.
- Replace the popup inspector section with normal tab DevTools: right-click inside the connector tab and select **Inspect**, or press the platform DevTools shortcut.
- Remove the warning that popup DevTools keeps the popup alive; normal tab DevTools does not define connector lifetime.
- State that the connector tab itself must be closed when a case requires a clean tab lifetime.

Use this replacement text for the installation confirmation:

```markdown
6. Select the pinned action. Chrome opens a connector tab; selecting the action
   again focuses the same tab. Confirm the status line reads **Protocol
   self-check passed** before selecting **Connect phone**. If the self-check
   fails, reload the extension and re-check; if it still fails, report an
   environment failure.
```

Use this replacement troubleshooting section:

```markdown
### Connector-tab DevTools (troubleshooting only)

To inspect connector errors, open DevTools for the connector tab (right-click
inside the page and select **Inspect**, or use the platform DevTools shortcut).
Read the console and the on-page event log. Close DevTools before timing a
physical case so debugging overhead does not affect observations. Closing
DevTools does not close the connector; close the connector tab itself when a
case requires a fresh tab lifetime.
```

- [ ] **Step 3: Convert all eight cases to connector-tab terminology**

In `spikes/web-bluetooth-popup/README.md`, make these exact semantic changes:

- Cases 1 and 2: open the connector tab from the action; choose and connect from the tab; expect the tab to remain usable through pairing.
- Cases 3 and 4: rename to `Windows connector-tab closure` and `macOS connector-tab closure`; close the browser tab, wait for Android disconnect/expiry, select the action to create a fresh tab, verify no live session, and explicitly reselect the phone.
- Case 5: use one connector tab on each computer and close both tabs during reset.
- Cases 6 and 7: rename to `Two phones, one Windows connector tab` and `Two phones, one macOS connector tab`; retain both sessions in one tab.
- Case 8 and clipboard verification: replace popup references with connector-tab references.
- Replace `clean popup lifetime` with `clean connector-tab lifetime` and `popup closure` with `connector-tab closure` throughout the repetition instructions.

Do not rename the existing directory, HTML/JavaScript filenames, report filename, or PR branch; those are stable technical paths.

- [ ] **Step 4: Record the platform finding and update the validation matrix**

In `docs/spikes/2026-08-11-popup-web-bluetooth-validation.md`, add this section before `## Case results`:

```markdown
## Action-popup platform finding

The initial physical attempt called `navigator.bluetooth.requestDevice()`
directly from the action popup's Connect button. Chrome displayed no chooser
and rejected the promise with `NotFoundError: User cancelled the
requestDevice() chooser.`

Chromium's extension chooser implementation requires the requesting extension
document to be the active contents of a browser tab. An action popup is not a
tab, so Chrome returns without showing the chooser and reports cancellation
when the chooser controller is destroyed. The harness now opens the same
connector document in an extension tab. Results below apply only to that
revised launch context.
```

Update the case and repetition tables so:

- Cases 1 and 2 expect a fresh chooser from the connector tab and continued tab usability.
- Cases 3 and 4 are connector-tab closure/reopen cases.
- Cases 5 through 8 use connector-tab terminology.
- Repetitions begin from a clean connector-tab lifetime.
- The go/no-go rule refers to repeatable connector-tab failures and explicitly says popup-owned Web Bluetooth was ruled out by the platform finding.

Keep all hardware-dependent actual results as `Not run`; the reported action-popup finding does not prove tab behavior.

- [ ] **Step 5: Check documentation consistency**

Run:

```bash
git diff --check
git grep -n -E 'open the popup|reopen the popup|close the popup|popup closure|clean popup lifetime|one .* popup' -- spikes/web-bluetooth-popup/README.md docs/spikes/2026-08-11-popup-web-bluetooth-validation.md
```

Expected: `git diff --check` exits 0. The terminology search exits 1 with no matches. Historical phrases such as `action popup`, stable paths containing `popup`, and the report title may remain.

- [ ] **Step 6: Commit the validation handoff**

```bash
git add spikes/web-bluetooth-popup/README.md docs/spikes/2026-08-11-popup-web-bluetooth-validation.md docs/superpowers/specs/2026-08-11-popup-web-bluetooth-spike-design.md
git commit -m "docs: retarget Bluetooth spike validation to tab"
```

---

### Task 4: Run final verification and prepare the PR update

**Files:**
- Verify only; do not modify unrelated files.

**Interfaces:**
- Consumes: completed launcher, manifest, layout, and documentation tasks.
- Produces: verified commits ready to push to `origin/feat/77-popup-web-bluetooth-spike` and update PR #78.

- [ ] **Step 1: Run all extension checks**

```bash
node --test spikes/web-bluetooth-popup/protocol.test.mjs spikes/web-bluetooth-popup/launcher.test.mjs
node --check spikes/web-bluetooth-popup/protocol.mjs
node --check spikes/web-bluetooth-popup/popup.mjs
node --check spikes/web-bluetooth-popup/launcher.mjs
node --check spikes/web-bluetooth-popup/service-worker.mjs
node -e 'const fs=require("fs"); const m=JSON.parse(fs.readFileSync("spikes/web-bluetooth-popup/manifest.json","utf8")); if(m.manifest_version!==3||m.action.default_popup||m.permissions.includes("bluetooth")||m.background.service_worker!=="service-worker.mjs"||m.background.type!=="module") process.exit(1)'
```

Expected: 9 tests pass; all syntax and manifest checks exit 0.

- [ ] **Step 2: Run Android regression tests**

Use the existing executable work directory because this worktree may be on a filesystem where the default temporary directory is mounted `noexec`:

```bash
./gradlew testDebugUnitTest -Pveles.native.workdir=.tmp-exec
```

Expected: `BUILD SUCCESSFUL` and all debug unit tests pass.

- [ ] **Step 3: Build the debug APK**

```bash
./gradlew assembleDebug -Pveles.native.workdir=.tmp-exec
```

Expected: `BUILD SUCCESSFUL`.

- [ ] **Step 4: Inspect repository state and commit history**

```bash
git status --short --branch
git diff --check
git log --oneline -10
```

Expected: no tracked changes remain; `.tmp-exec/` may remain untracked and must not be staged. Recent history contains the design commit plus the three implementation commits from Tasks 1 through 3.

- [ ] **Step 5: Review the complete PR diff**

```bash
git diff --stat origin/master...HEAD
git diff origin/master...HEAD -- spikes/web-bluetooth-popup docs/spikes docs/superpowers/specs/2026-08-11-extension-tab-web-bluetooth-design.md docs/superpowers/specs/2026-08-11-popup-web-bluetooth-spike-design.md
```

Expected: the diff contains only the existing spike plus the intended tab-launch, tests, responsive CSS, and documentation changes; no secret or real OTP data is present.

- [ ] **Step 6: Push the implementation branch**

```bash
git push origin feat/77-popup-web-bluetooth-spike
```

Expected: push succeeds and PR #78 updates. Do not mark physical validation as passed or move the PR out of draft based only on automated checks.
