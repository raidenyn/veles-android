import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { openConnector } from "./launcher.mjs";

test("creates the connector tab when none exists", async () => {
  const calls = [];
  const chromeApi = {
    runtime: {
      getURL: (path) => `chrome-extension://test/${path}`,
      getContexts: async (filter) => {
        calls.push(["getContexts", filter]);
        return [];
      },
    },
    tabs: {
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
    [
      "getContexts",
      {
        contextTypes: ["TAB"],
        documentUrls: ["chrome-extension://test/popup.html"],
      },
    ],
    ["create", { url: "chrome-extension://test/popup.html" }],
  ]);
});

test("activates and focuses an existing connector tab", async () => {
  const calls = [];
  const chromeApi = {
    runtime: {
      getURL: (path) => `chrome-extension://test/${path}`,
      getContexts: async (filter) => {
        calls.push(["getContexts", filter]);
        return [{ tabId: 17, windowId: 23 }];
      },
    },
    tabs: {
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
    [
      "getContexts",
      {
        contextTypes: ["TAB"],
        documentUrls: ["chrome-extension://test/popup.html"],
      },
    ],
    ["update-window", 23, { focused: true }],
    ["update-tab", 17, { active: true }],
  ]);
});

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
