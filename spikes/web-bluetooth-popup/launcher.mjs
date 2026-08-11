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
