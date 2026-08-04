"use strict";

importScripts("core.js");

const DEFAULTS = {
  baseUrl: "http://127.0.0.1:8765",
  token: "",
  activeJobId: "",
  activeJobTitle: "",
  activeQuestion: "",
  anonymize: true,
  maxCandidates: 25,
  collectionStopped: false,
  localCandidates: []
};

chrome.runtime.onInstalled.addListener(async () => {
  const settings = await chrome.storage.local.get(DEFAULTS);
  await chrome.storage.local.set(settings);
  chrome.contextMenus.create({
    id: "save-to-oyster",
    title: "Review in Research Oyster",
    contexts: ["selection"]
  });
  ensureAutoApproveAlarm();
});

chrome.runtime.onStartup.addListener(ensureAutoApproveAlarm);

function ensureAutoApproveAlarm() {
  // Standing-consent poll: checks for session requests on domains the user pre-trusted and
  // starts recording them hands-free. Only acts when a token exists; capture still happens
  // only in this browser on pages the user has open.
  chrome.alarms.create("oyster-autoapprove", { periodInMinutes: 1 });
}

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId !== "save-to-oyster" || !info.selectionText || !tab?.id) return;
  const settings = await chrome.storage.local.get(DEFAULTS);
  if (settings.collectionStopped) {
    await setNotice("Collection is stopped. Open Oyster Capture to resume.", "warning");
    return;
  }
  if (!settings.activeJobId) {
    await setNotice("Choose an active research job before capturing a selection.", "warning");
    return;
  }
  try {
    const candidate = OysterCaptureCore.capture({
      excerpt: info.selectionText,
      url: info.pageUrl,
      page_title: tab.title,
      job_id: settings.activeJobId,
      job_title: settings.activeJobTitle,
      research_question: settings.activeQuestion,
      anonymized: settings.anonymize,
      capture_mode: "manual"
    });
    await addCandidates([candidate], settings.maxCandidates);
    await setNotice("Selection is ready for review. Nothing has been sent yet.", "success");
    chrome.action.setBadgeText({ text: "1", tabId: tab.id });
    chrome.action.setBadgeBackgroundColor({ color: "#C46A2B", tabId: tab.id });
  } catch (error) {
    await setNotice(error.message, "error");
  }
});

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  (async () => {
    if (message.type === "ADD_CANDIDATES") {
      const settings = await chrome.storage.local.get(DEFAULTS);
      if (settings.collectionStopped) throw new Error("Collection is stopped.");
      const candidates = message.items.map(item => OysterCaptureCore.capture({
        ...item,
        job_id: settings.activeJobId,
        job_title: settings.activeJobTitle,
        research_question: settings.activeQuestion,
        anonymized: settings.anonymize
      }));
      const count = await addCandidates(candidates, settings.maxCandidates);
      return { ok: true, count };
    }
    if (message.type === "NOTICE") {
      await setNotice(message.text, message.level);
      return { ok: true };
    }
    if (message.type === "SESSION_START") {
      return await startSession(message.session);
    }
    if (message.type === "SESSION_STOP") {
      await stopSession(message.session_id, "user");
      return { ok: true };
    }
    if (message.type === "AUTO_APPROVE") {
      await autoApproveTick();
      return { ok: true };
    }
    if (message.type === "SESSION_NET_CAPTURE") {
      await handleNetworkCapture(message.payload, sender);
      return { ok: true };
    }
    return { ok: false };
  })().then(respond).catch(error => respond({ ok: false, error: error.message }));
  return true;
});

async function apiBase() {
  const { baseUrl, token } = await chrome.storage.local.get({ baseUrl: DEFAULTS.baseUrl, token: "" });
  return { base: OysterCaptureCore.normalizeBaseUrl(baseUrl), token };
}

async function startSession(session) {
  if (!session || !session.session_id || !session.domain) return { ok: false, error: "Invalid session." };
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) return { ok: false, error: "Open the page you want to record first." };
  // Chrome's own permission prompt is a second, independent consent layer for this domain.
  const origins = [`https://${session.domain}/*`, `https://*.${session.domain}/*`];
  const granted = await chrome.permissions.request({ origins });
  if (!granted) return { ok: false, error: "Domain permission was declined, so nothing will be recorded." };
  const active = {
    id: session.session_id, domain: session.domain, jobId: session.job_id,
    maxPayloadBytes: session.max_payload_bytes || 131072, expiresAt: session.expires_at, seen: []
  };
  await chrome.storage.local.set({ activeSession: active });
  await injectRecorder(tab.id, active);
  const expiresMs = Date.parse(session.expires_at || "") || (Date.now() + 30 * 60000);
  chrome.alarms.create(`oyster-session-${active.id}`, { when: expiresMs });
  return { ok: true };
}

async function injectRecorder(tabId, active) {
  await chrome.scripting.executeScript({
    target: { tabId }, world: "MAIN",
    func: (config) => { window.__oysterSession = config; },
    args: [{ domain: active.domain, maxPayloadBytes: active.maxPayloadBytes }]
  });
  await chrome.scripting.executeScript({ target: { tabId }, world: "MAIN", files: ["session_recorder.js"] });
  await chrome.scripting.executeScript({ target: { tabId }, files: ["core.js", "session_relay.js"] });
}

async function handleNetworkCapture(payload, sender) {
  const settings = await chrome.storage.local.get(DEFAULTS);
  const { activeSession } = await chrome.storage.local.get({ activeSession: null });
  if (!activeSession || settings.collectionStopped) return;
  if (Date.parse(activeSession.expiresAt || "") && Date.parse(activeSession.expiresAt) <= Date.now()) {
    await stopSession(activeSession.id, "expired");
    return;
  }
  let host = "";
  try { host = new URL(payload.url).hostname; } catch (_) { return; }
  if (!OysterCaptureCore.domainMatches(host, activeSession.domain)) return;
  const fp = OysterCaptureCore.payloadFingerprint(payload.url, payload.body);
  const seen = new Set(activeSession.seen || []);
  if (seen.has(fp) || seen.size >= 50) return;  // bound per-session volume
  seen.add(fp);
  activeSession.seen = [...seen];
  await chrome.storage.local.set({ activeSession });

  let json;
  try { json = JSON.parse(payload.body); } catch (_) { return; }
  const excerpt = OysterCaptureCore.extractMessages(json, 25, 12000);
  if (!excerpt) return;

  const capture = OysterCaptureCore.capture({
    excerpt,
    url: sender?.tab?.url || payload.url,
    page_title: sender?.tab?.title || activeSession.domain,
    job_id: activeSession.jobId,
    job_title: settings.activeJobTitle,
    research_question: settings.activeQuestion,
    anonymized: settings.anonymize,
    capture_mode: "approved_session",
    metadata: {
      session_id: activeSession.id,
      network: { url: OysterCaptureCore.redactUrl(payload.url), method: payload.method,
                 status: payload.status, content_type: payload.contentType, body: payload.body }
    }
  });

  try {
    const { base, token } = await apiBase();
    const response = await fetch(`${base}/api/capture/approve`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Authorization": `Bearer ${token}` },
      body: JSON.stringify(OysterCaptureCore.approvalPayload(capture))
    });
    if (response.status === 403) { await stopSession(activeSession.id, "revoked"); return; }
    if (response.ok) await setNotice(`Captured network evidence from ${activeSession.domain}.`, "success");
  } catch (_) { /* transient; next payload retries */ }
}

async function stopSession(sessionId, reason) {
  const { activeSession } = await chrome.storage.local.get({ activeSession: null });
  try {
    const { base, token } = await apiBase();
    await fetch(`${base}/api/capture/sessions/${encodeURIComponent(sessionId)}/stop`, {
      method: "POST", headers: { "Content-Type": "application/json", "Authorization": `Bearer ${token}` }, body: "{}"
    });
  } catch (_) { /* server may already have ended it */ }
  // Keep the Chrome host permission for a standing-consent (trusted) domain; the user removes
  // it explicitly via Unpair or by removing the trusted domain in settings.
  if (activeSession && activeSession.domain && !activeSession.standing) {
    try { await chrome.permissions.remove({ origins: [`https://${activeSession.domain}/*`, `https://*.${activeSession.domain}/*`] }); } catch (_) {}
  }
  chrome.alarms.clear(`oyster-session-${sessionId}`);
  await chrome.storage.local.set({ activeSession: null });
}

// Standing-consent auto-approve: approve trusted-domain session requests and start recording
// on any open tab already on that domain. Never opens or navigates tabs itself.
async function autoApproveTick() {
  const { token, collectionStopped } = await chrome.storage.local.get({ token: "", collectionStopped: false });
  if (!token || collectionStopped) return;
  let approved = [];
  try {
    const { base } = await apiBase();
    const r = await fetch(`${base}/api/capture/sessions/auto-approve`, {
      method: "POST", headers: { "Content-Type": "application/json", "Authorization": `Bearer ${token}` }, body: "{}"
    });
    if (!r.ok) return;
    approved = (await r.json()).approved || [];
  } catch (_) { return; }
  for (const session of approved) await startTrustedSession(session);
}

async function startTrustedSession(session) {
  const active = {
    id: session.session_id, domain: session.domain, jobId: session.job_id,
    maxPayloadBytes: session.max_payload_bytes || 131072, expiresAt: session.expires_at,
    seen: [], standing: true
  };
  await chrome.storage.local.set({ activeSession: active });
  const expiresMs = Date.parse(session.expires_at || "") || (Date.now() + 30 * 60000);
  chrome.alarms.create(`oyster-session-${active.id}`, { when: expiresMs });
  // Inject into tabs already open on the trusted domain (permission was granted at trust time).
  try {
    const tabs = await chrome.tabs.query({ url: [`https://${active.domain}/*`, `https://*.${active.domain}/*`] });
    for (const tab of tabs) { if (tab.id) await injectRecorder(tab.id, active).catch(() => {}); }
  } catch (_) { /* no matching tab open yet; onUpdated will inject on navigation */ }
}

// Keep capturing as the user navigates within an active session's domain.
chrome.tabs.onUpdated.addListener(async (tabId, info, tab) => {
  if (info.status !== "complete" || !tab?.url) return;
  const { activeSession, collectionStopped } = await chrome.storage.local.get({ activeSession: null, collectionStopped: false });
  if (!activeSession || collectionStopped) return;
  let host = "";
  try { host = new URL(tab.url).hostname; } catch (_) { return; }
  if (OysterCaptureCore.domainMatches(host, activeSession.domain)) {
    injectRecorder(tabId, activeSession).catch(() => {});
  }
});

chrome.alarms.onAlarm.addListener(alarm => {
  if (alarm.name === "oyster-autoapprove") { autoApproveTick(); return; }
  const match = /^oyster-session-(\d+)$/.exec(alarm.name || "");
  if (match) stopSession(Number(match[1]), "expired");
});

async function addCandidates(items, maxCandidates) {
  const { localCandidates = [] } = await chrome.storage.local.get("localCandidates");
  const next = OysterCaptureCore.dedupeCaptures(localCandidates, items, maxCandidates);
  await chrome.storage.local.set({ localCandidates: next });
  const before = new Set(localCandidates.map(OysterCaptureCore.fingerprint));
  return next.filter(item => !before.has(OysterCaptureCore.fingerprint(item))).length;
}

async function setNotice(text, level) {
  await chrome.storage.local.set({ notice: { text, level, at: Date.now() } });
}
