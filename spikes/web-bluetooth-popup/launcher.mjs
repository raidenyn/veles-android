export async function openConnector(chromeApi) {
  const url = chromeApi.runtime.getURL("popup.html");
  // tabs.query({ url }) silently ignores the url filter without the "tabs"
  // permission or host permissions (even for the extension's own
  // chrome-extension:// pages), which this extension deliberately does not
  // request. runtime.getContexts() enumerates only the extension's own
  // contexts and needs no manifest permission (Chrome 116+).
  const [existing] = await chromeApi.runtime.getContexts({
    contextTypes: ["TAB"],
    documentUrls: [url],
  });
  if (existing) {
    await chromeApi.windows.update(existing.windowId, { focused: true });
    await chromeApi.tabs.update(existing.tabId, { active: true });
    return;
  }
  await chromeApi.tabs.create({ url });
}
