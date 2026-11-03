// The service worker owns no Native Messaging port: the connector tab owns it, so closing the tab
// is a real Chrome-owned lifecycle trigger. It only opens the connector page on demand.
chrome.action.onClicked.addListener(() => {
    chrome.tabs.create({ url: chrome.runtime.getURL("connector.html") });
});
