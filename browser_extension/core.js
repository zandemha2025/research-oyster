(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.OysterCaptureCore = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const SOURCE_RULES = [
    [/(^|\.)discord\.com$/i, "discord_supervised"],
    [/(^|\.)(x\.com|twitter\.com)$/i, "x_supervised"],
    [/(^|\.)twitch\.tv$/i, "twitch_supervised"],
    [/(^|\.)kick\.com$/i, "kick_supervised"],
    [/(^|\.)reddit\.com$/i, "reddit_supervised"]
  ];

  function sourceType(url) {
    try {
      const host = new URL(url).hostname;
      const found = SOURCE_RULES.find(([rule]) => rule.test(host));
      return found ? found[1] : "web_supervised";
    } catch (_) {
      return "web_supervised";
    }
  }

  function cleanText(value, maxLength) {
    return String(value || "").replace(/\s+/g, " ").trim().slice(0, maxLength || 4000);
  }

  function canonicalUrl(value) {
    try {
      const url = new URL(String(value || ""));
      url.hash = "";
      ["utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "ref", "ref_src"].forEach(key => url.searchParams.delete(key));
      url.searchParams.sort();
      return url.toString().replace(/\/$/, "");
    } catch (_) { return String(value || "").trim(); }
  }

  function fingerprint(item) {
    return `${canonicalUrl(item.url)}|${cleanText(item.raw_excerpt || item.excerpt).toLocaleLowerCase()}`;
  }

  function dedupeCaptures(existing, incoming, maxItems) {
    const output = [...(existing || [])];
    const seen = new Set(output.map(fingerprint));
    for (const item of incoming || []) {
      const key = fingerprint(item);
      if (!key || seen.has(key)) continue;
      seen.add(key);
      output.push(item);
    }
    return output.slice(-Math.max(1, Number(maxItems) || 25));
  }

  function approvalPayload(item) {
    if (!item?.client_capture_id || !item?.job_id) throw new Error("Approved captures require their original job and capture ID.");
    const payload = { ...item, excerpt: cleanText(item.excerpt), researcher_note: cleanText(item.researcher_note, 1000) || null, approved_by_user: true };
    if (!payload.excerpt) throw new Error("The edited excerpt cannot be empty.");
    if (payload.anonymized) delete payload.raw_excerpt;
    return payload;
  }

  function anonymize(text) {
    let i = 0;
    const aliases = new Map();
    return cleanText(text).replace(/(^|\n|\s)@?([A-Za-z0-9_.-]{2,32})(?=:)/g, (all, lead, name) => {
      const key = name.toLowerCase();
      if (!aliases.has(key)) aliases.set(key, `Participant ${++i}`);
      return `${lead}${aliases.get(key)}`;
    });
  }

  function normalizeBaseUrl(value) {
    const parsed = new URL(String(value || "http://127.0.0.1:8765"));
    if (!["127.0.0.1", "localhost"].includes(parsed.hostname) || parsed.protocol !== "http:") {
      throw new Error("Oyster must use a local http://127.0.0.1 or localhost address.");
    }
    return parsed.origin;
  }

  function capture(input) {
    const now = new Date().toISOString();
    const excerpt = cleanText(input.excerpt);
    if (!excerpt) throw new Error("Nothing was selected or found to capture.");
    return {
      client_capture_id: input.client_capture_id || (globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`),
      job_id: input.job_id || null,
      job_title: cleanText(input.job_title, 500) || null,
      research_question: cleanText(input.research_question, 500) || null,
      source_type: sourceType(input.url),
      url: String(input.url || ""),
      page_title: cleanText(input.page_title, 500),
      excerpt: input.anonymized === false ? excerpt : anonymize(excerpt),
      raw_excerpt: excerpt,
      researcher_note: cleanText(input.researcher_note, 1000) || null,
      captured_at: input.captured_at || now,
      capture_mode: input.capture_mode || "manual",
      anonymized: input.anonymized !== false,
      approved_by_user: false
    };
  }

  function score(text, terms) {
    const haystack = cleanText(text).toLowerCase();
    if (!haystack) return 0;
    return (terms || []).reduce((total, term) => {
      const needle = cleanText(term, 100).toLowerCase();
      return total + (needle && haystack.includes(needle) ? 1 : 0);
    }, 0);
  }

  return { sourceType, cleanText, canonicalUrl, fingerprint, dedupeCaptures, approvalPayload, anonymize, normalizeBaseUrl, capture, score };
});
