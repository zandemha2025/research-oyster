"use strict";

if (!globalThis.__oysterCaptureInjected) {
globalThis.__oysterCaptureInjected = true;
chrome.runtime.onMessage.addListener((message, _sender, respond) => {
  if (message.type !== "SCAN_VISIBLE") return false;
  try {
    const terms = Array.isArray(message.terms) ? message.terms : [];
    const max = Math.min(Math.max(Number(message.max) || 10, 1), 25);
    const nodes = platformNodes(OysterCaptureCore.sourceType(location.href));
    const items = [];
    const seen = new Set();
    for (const node of nodes) {
      if (!isVisible(node)) continue;
      const text = OysterCaptureCore.cleanText(node.innerText || node.textContent, 2000);
      if (text.length < 20 || seen.has(text)) continue;
      const relevance = OysterCaptureCore.score(text, terms);
      if (terms.length && relevance === 0) continue;
      seen.add(text);
      items.push({
        excerpt: text,
        url: location.href,
        page_title: document.title,
        capture_mode: "supervised",
        relevance
      });
      if (items.length >= max) break;
    }
    respond({ ok: true, items });
  } catch (error) {
    respond({ ok: false, error: error.message });
  }
  return true;
});

function platformNodes(source) {
  const selectors = {
    discord_supervised: "li[id^='chat-messages'], [class*='messageListItem']",
    x_supervised: "article[data-testid='tweet']",
    twitch_supervised: "[data-a-target='chat-line-message'], [data-test-selector='chat-line-message']",
    kick_supervised: "[data-testid='chat-message'], [class*='chat-entry'], [class*='chatMessage']",
    reddit_supervised: "shreddit-comment, shreddit-post, [data-testid='comment']",
    web_supervised: "article, main p, main li, [role='article']"
  };
  const found = Array.from(document.querySelectorAll(selectors[source]));
  return found.length ? found : Array.from(document.querySelectorAll("main p, article, [role='article']"));
}

function isVisible(node) {
  const rect = node.getBoundingClientRect();
  const style = getComputedStyle(node);
  return rect.width > 0 && rect.height > 0 && rect.bottom >= 0 && rect.top <= innerHeight && style.visibility !== "hidden";
}
}
