"use strict";
// Runs in the isolated content-script world of an approved-session tab. It listens for the
// recorder's window messages (posted from the MAIN world) and forwards them to the service
// worker. Keeping this in the isolated world means the page cannot call chrome.* directly.
(function () {
  window.addEventListener("message", event => {
    if (event.source !== window) return;
    const data = event.data;
    if (!data || data.type !== "OYSTER_NET_CAPTURE" || !data.payload) return;
    try {
      chrome.runtime.sendMessage({ type: "SESSION_NET_CAPTURE", payload: data.payload });
    } catch (_) { /* worker may be asleep; next payload will retry */ }
  });
})();
