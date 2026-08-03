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
});

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
    return { ok: false };
  })().then(respond).catch(error => respond({ ok: false, error: error.message }));
  return true;
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
