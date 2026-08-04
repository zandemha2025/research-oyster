"use strict";
// Runs in the MAIN world of an approved-session tab. It wraps the page's own fetch and
// XMLHttpRequest so JSON responses the page already receives can be surfaced for capture.
// It never reads cookies, request headers, or credentials, and it only forwards responses
// whose host matches the session's approved domain. Because the page already controls its
// own network payloads, spoofing this channel grants no capability the page did not have.
(function () {
  const state = window.__oysterSession;
  if (!state || !state.domain) return;
  if (window.__oysterRecorderInstalled) return;
  window.__oysterRecorderInstalled = true;

  const MAX_BODY = Math.max(1024, Number(state.maxPayloadBytes) || 131072);

  function hostMatches(rawUrl) {
    try {
      const host = new URL(rawUrl, location.href).hostname.toLowerCase();
      const domain = String(state.domain).toLowerCase();
      return host === domain || host.endsWith(`.${domain}`);
    } catch (_) { return false; }
  }

  function offer(url, method, status, contentType, body) {
    try {
      if (!body || body.length > MAX_BODY) return;
      if (!/json/i.test(contentType || "")) return;
      window.postMessage({
        type: "OYSTER_NET_CAPTURE",
        payload: { url: String(url), method: String(method || "GET"), status: Number(status) || 0,
                   contentType: String(contentType || ""), body: String(body) }
      }, location.origin);
    } catch (_) { /* never disturb the page */ }
  }

  const originalFetch = window.fetch;
  if (typeof originalFetch === "function") {
    window.fetch = function (...args) {
      return originalFetch.apply(this, args).then(response => {
        try {
          const url = (response && response.url) || (typeof args[0] === "string" ? args[0] : args[0]?.url);
          if (url && hostMatches(url)) {
            const clone = response.clone();
            const contentType = clone.headers.get("content-type") || "";
            if (/json/i.test(contentType)) {
              clone.text().then(text => offer(url, (args[1] && args[1].method) || "GET", response.status, contentType, text)).catch(() => {});
            }
          }
        } catch (_) { /* ignore */ }
        return response;
      });
    };
  }

  const OriginalXHR = window.XMLHttpRequest;
  if (typeof OriginalXHR === "function") {
    const open = OriginalXHR.prototype.open;
    const send = OriginalXHR.prototype.send;
    OriginalXHR.prototype.open = function (method, url) {
      this.__oysterMethod = method; this.__oysterUrl = url;
      return open.apply(this, arguments);
    };
    OriginalXHR.prototype.send = function () {
      this.addEventListener("load", () => {
        try {
          if (this.__oysterUrl && hostMatches(this.__oysterUrl)) {
            const contentType = this.getResponseHeader("content-type") || "";
            if (/json/i.test(contentType)) offer(this.__oysterUrl, this.__oysterMethod, this.status, contentType, this.responseText);
          }
        } catch (_) { /* ignore */ }
      });
      return send.apply(this, arguments);
    };
  }
})();
