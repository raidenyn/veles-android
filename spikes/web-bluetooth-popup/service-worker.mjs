import { openConnector } from "./launcher.mjs";

chrome.action.onClicked.addListener(() => {
  openConnector(chrome).catch((error) => {
    console.error("Failed to open connector tab", error);
  });
});
