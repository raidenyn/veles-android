// The service worker deliberately owns no Native Messaging port (RFC: the connector tab owns it).
// It only opens the connector page so a human can drive the harness by hand.
chrome.action.onClicked.addListener(() => {
    chrome.tabs.create({ url: chrome.runtime.getURL("connector.html") });
});
