// Disposable probe. mode=port opens a Native Messaging port; mode=control does not. Both run the same
// 1 s setTimeout chain and record page-clock samples in memory only (no storage, no network, no audio,
// no WebRTC, no WebSocket); the driver reads window.__log after the idle interval.
const mode = new URLSearchParams(location.search).get("mode") || "control";
const HOST = "me.nagaev.veles.timerprobe";
const log = (window.__log = {
  mode,
  started_epoch_ms: Date.now(),
  timers: [], // performance.now() at each setTimeout callback
  messages: [], // {recv_perf, recv_epoch_ms, seq, host_epoch_ms}
  visibility: [{ perf: performance.now(), state: document.visibilityState, focus: document.hasFocus() }],
  disconnects: [],
});
document.addEventListener("visibilitychange", () =>
  log.visibility.push({ perf: performance.now(), state: document.visibilityState, focus: document.hasFocus() }),
);
(function tick() {
  log.timers.push(performance.now());
  setTimeout(tick, 1000);
})();
if (mode === "port") {
  const port = chrome.runtime.connectNative(HOST);
  port.onMessage.addListener((m) =>
    log.messages.push({
      recv_perf: performance.now(),
      recv_epoch_ms: Date.now(),
      seq: m.seq,
      host_epoch_ms: m.t,
    }),
  );
  port.onDisconnect.addListener(() =>
    log.disconnects.push({ perf: performance.now(), error: chrome.runtime.lastError?.message || null }),
  );
}
