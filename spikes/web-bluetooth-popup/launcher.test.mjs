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
