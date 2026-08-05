"use strict";

const $ = selector => document.querySelector(selector);
const defaults = { baseUrl:"http://127.0.0.1:8765", token:"", activeJobId:"", activeJobTitle:"", activeQuestion:"", anonymize:true, maxCandidates:25, collectionStopped:false, localCandidates:[] };
let state = {};

document.addEventListener("DOMContentLoaded", init);

async function init() {
  state = await chrome.storage.local.get(defaults);
  $("#settings").onclick = () => chrome.runtime.openOptionsPage();
  $("#refresh").onclick = loadJobs;
  $("#job").onchange = selectJob;
  $("#question").onchange = selectQuestion;
  $("#scan").onclick = scan;
  $("#stop").onclick = toggleStop;
  $("#clear").onclick = clearAll;
  chrome.storage.onChanged.addListener(async changes => {
    if (changes.localCandidates || changes.notice) { state = await chrome.storage.local.get(defaults); renderQueue(); renderNotice(); }
    if (changes.activeSession) renderActiveSession(changes.activeSession.newValue);
  });
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    state.currentUrl = tab?.url || "";
    try { state.currentHost = new URL(state.currentUrl).hostname; } catch (_) { state.currentHost = ""; }
  } catch (_) { state.currentHost = ""; }
  renderStatus(); renderQueue(); renderNotice();
  if (state.token) { await loadJobs(); await loadSessions(); }
}

async function loadSessions() {
  if (!state.token) return;
  try { await chrome.runtime.sendMessage({ type: "AUTO_APPROVE" }); } catch (_) { /* worker asleep */ }
  try {
    const requests = await api("/api/capture/sessions");
    renderSessionRequests(Array.isArray(requests) ? requests : []);
  } catch (error) { /* non-fatal: sessions are optional */ }
  const { activeSession } = await chrome.storage.local.get({ activeSession: null });
  renderActiveSession(activeSession);
  renderCaptureNow(activeSession);
}

function renderCaptureNow(activeSession) {
  const el = document.getElementById("captureNow");
  const pill = document.getElementById("captureState");
  if (!el) return;
  if (activeSession && activeSession.id) {
    if (pill) { pill.textContent = "Recording"; pill.className = "pill good"; }
    el.innerHTML = "";  // active state is shown by renderActiveSession (with Stop)
    return;
  }
  if (pill) { pill.textContent = "Off"; pill.className = "pill neutral"; }
  const host = state.currentHost || "";
  if (!state.activeJobId) { el.innerHTML = '<p class="muted">Pick a research job above to enable capture.</p>'; return; }
  if (!host || !/^https?:/.test(state.currentUrl || "")) { el.innerHTML = '<p class="muted">Open the site you want to capture in this tab.</p>'; return; }
  el.innerHTML = `<button id="startCapture" class="primary">Start capturing ${escapeHtml(host)}</button>`;
  document.getElementById("startCapture").onclick = () => startCaptureThisSite(host);
}

async function startCaptureThisSite(host) {
  setBusy(true);
  try {
    const granted = await chrome.permissions.request({ origins: [`https://${host}/*`, `https://*.${host}/*`] });
    if (!granted) throw new Error("Chrome permission was declined, so nothing will be captured.");
    const result = await api("/api/capture/sessions/start", {
      method: "POST", body: JSON.stringify({ job_id: Number(state.activeJobId), domain: host, approved_by_user: true })
    });
    const response = await chrome.runtime.sendMessage({ type: "SESSION_START", session: result });
    if (!response?.ok) throw new Error(response?.error || "Could not start recording on this site.");
    show(`Recording ${result.domain} for 30 minutes. Browse normally; click Stop any time.`, "success");
    await loadSessions();
  } catch (error) { show(error.message, "error"); }
  finally { setBusy(false); }
}

function renderSessionRequests(sessions) {
  const pending = sessions.filter(s => s.status === "requested");
  const el = $("#sessionRequests");
  if (!pending.length) { el.innerHTML = ""; return; }
  el.innerHTML = pending.map(s => `<article class="card">
    <div class="meta"><strong>${escapeHtml(s.domain)}</strong> · job ${escapeHtml(s.job_id)}</div>
    <div class="meta">${escapeHtml(s.reason || "")}</div>
    <label class="toggle"><input type="checkbox" class="session-always" data-id="${escapeHtml(s.id)}"><span>Always capture ${escapeHtml(s.domain)} (approve once)</span></label>
    <div class="actions"><button class="secondary session-approve" data-id="${escapeHtml(s.id)}" data-domain="${escapeHtml(s.domain)}" data-job="${escapeHtml(s.job_id)}">Approve (30 min)</button><button class="danger session-decline" data-id="${escapeHtml(s.id)}">Decline</button></div></article>`).join("");
  document.querySelectorAll(".session-approve").forEach(b => b.onclick = () => approveSession(b.dataset.id, b.dataset.domain));
  document.querySelectorAll(".session-decline").forEach(b => b.onclick = () => declineSession(b.dataset.id));
}

function renderActiveSession(session) {
  const el = $("#activeSession");
  if (!session || !session.id) { el.hidden = true; el.innerHTML = ""; return; }
  el.hidden = false;
  el.innerHTML = `<article class="card"><div class="meta"><strong>Recording ${escapeHtml(session.domain)}</strong></div>
    <div class="meta">Ends ${escapeHtml(formatTime(session.expiresAt))}</div>
    <div class="actions"><button class="danger session-stop" data-id="${escapeHtml(session.id)}">Stop recording</button></div></article>`;
  el.querySelector(".session-stop").onclick = () => stopSession(session.id);
}

async function approveSession(sessionId, domain) {
  setBusy(true);
  try {
    const always = document.querySelector(`.session-always[data-id="${sessionId}"]`)?.checked;
    if (always && domain) {
      // Standing consent: Chrome's own host prompt is a second consent for the domain.
      const granted = await chrome.permissions.request({ origins: [`https://${domain}/*`, `https://*.${domain}/*`] });
      if (granted) await api("/api/capture/trusted-domains", { method: "POST", body: JSON.stringify({ domain, approved_by_user: true }) });
    }
    const result = await api(`/api/capture/sessions/${encodeURIComponent(sessionId)}/approve`, {
      method: "POST", body: JSON.stringify({ approved_by_user: true, ttl_minutes: 30 })
    });
    const response = await chrome.runtime.sendMessage({ type: "SESSION_START", session: result });
    if (!response?.ok) throw new Error(response?.error || "Could not start recording on this domain.");
    const suffix = always ? " Future sessions on this domain will start hands-free." : "";
    show(`Recording ${result.domain} until the session ends.${suffix}`, "success");
    await loadSessions();
  } catch (error) { show(error.message, "error"); }
  finally { setBusy(false); }
}

async function declineSession(sessionId) {
  try { await api(`/api/capture/sessions/${encodeURIComponent(sessionId)}/decline`, { method: "POST", body: "{}" }); await loadSessions(); }
  catch (error) { show(error.message, "error"); }
}

async function stopSession(sessionId) {
  try { await chrome.runtime.sendMessage({ type: "SESSION_STOP", session_id: sessionId }); show("Recording stopped.", "success"); await loadSessions(); }
  catch (error) { show(error.message, "error"); }
}

async function api(path, options={}) {
  const base = OysterCaptureCore.normalizeBaseUrl(state.baseUrl);
  const response = await fetch(`${base}${path}`, {
    ...options,
    headers:{ "Content-Type":"application/json", "Authorization":`Bearer ${state.token}`, ...(options.headers || {}) }
  });
  if (!response.ok) { const body = await response.json().catch(()=>({})); throw new Error(body.detail || body.error || `Oyster returned ${response.status}.`); }
  return response.status === 204 ? null : response.json();
}

async function loadJobs() {
  if (!state.token) { show("Pair this extension in Settings first.", "error"); renderStatus(false); return; }
  setBusy(true);
  try {
    const data = await api("/api/capture/jobs");
    const jobs = Array.isArray(data) ? data : (data.jobs || []);
    $("#job").innerHTML = '<option value="">Choose a job…</option>' + jobs.map(job => `<option value="${escapeHtml(job.id)}">${escapeHtml(job.title || job.brief || `Job ${job.id}`)}</option>`).join("");
    $("#job").value = String(state.activeJobId || "");
    if (state.activeJobId) {
      state.activeJobTitle = $("#job").selectedOptions[0]?.textContent || state.activeJobTitle;
      await chrome.storage.local.set({activeJobTitle:state.activeJobTitle});
    }
    renderStatus(true);
    if (state.activeJobId) await loadMission();
  } catch (error) { show(error.message, "error"); renderStatus(false); }
  finally { setBusy(false); }
}

async function selectJob() {
  state.activeJobId = $("#job").value;
  state.activeJobTitle = $("#job").selectedOptions[0]?.textContent || "";
  state.activeQuestion = "";
  await chrome.storage.local.set({ activeJobId:state.activeJobId, activeJobTitle:state.activeJobTitle, activeQuestion:"" });
  await loadMission();
}

async function loadMission() {
  if (!state.activeJobId) { $("#question").disabled=true; $("#question").innerHTML='<option value="">Choose a job first…</option>'; $("#mission").textContent = "Select a research job to see its mission."; return; }
  try {
    const data = await api(`/api/capture/jobs/${encodeURIComponent(state.activeJobId)}/mission`);
    const questions = data.research_questions || (data.research_question ? [data.research_question] : data.question ? [data.question] : []);
    state.activeQuestion = questions.includes(state.activeQuestion) ? state.activeQuestion : (questions[0] || "");
    state.missionTerms = data.look_for || data.search_terms || data.terms || [];
    await chrome.storage.local.set({ activeQuestion:state.activeQuestion, missionTerms:state.missionTerms });
    $("#question").innerHTML = questions.map(question=>`<option value="${escapeHtml(question)}">${escapeHtml(question)}</option>`).join("") || '<option value="">No question supplied</option>';
    $("#question").value=state.activeQuestion; $("#question").disabled=!questions.length;
    const lines = [data.brief || data.summary || data.mission || state.activeQuestion, state.missionTerms.length ? `Look for: ${state.missionTerms.join(", ")}` : ""].filter(Boolean);
    $("#mission").textContent = lines.join("\n");
  } catch (error) { show(error.message, "error"); }
}

async function selectQuestion(){ state.activeQuestion=$("#question").value; await chrome.storage.local.set({activeQuestion:state.activeQuestion}); }

async function scan() {
  if (state.collectionStopped) { show("Collection is stopped. Press Resume before scanning.", "error"); return; }
  if (!state.activeJobId) { show("Choose an active research job first.", "error"); return; }
  const [tab] = await chrome.tabs.query({ active:true, currentWindow:true });
  if (!tab?.id || !/^https?:/.test(tab.url || "")) { show("Open a supported web page before scanning.", "error"); return; }
  setBusy(true);
  try {
    await chrome.scripting.executeScript({ target:{ tabId:tab.id }, files:["core.js", "content.js"] });
    const result = await chrome.tabs.sendMessage(tab.id, { type:"SCAN_VISIBLE", terms:state.missionTerms || [], max:Math.min(state.maxCandidates, 25) });
    if (!result?.ok) throw new Error(result?.error || "The page could not be scanned. Reload it and try again.");
    if (!result.items.length) { show("No visible passages matched this mission. Scroll, adjust the mission, or capture a selection.", "warning"); return; }
    const response = await chrome.runtime.sendMessage({ type:"ADD_CANDIDATES", items:result.items });
    if (!response?.ok) throw new Error(response?.error || "Candidates could not be queued.");
    state = await chrome.storage.local.get(defaults); renderQueue(); show(`${response.count} candidate${response.count===1?"":"s"} ready for review.`, "success");
  } catch (error) { show(error.message, "error"); }
  finally { setBusy(false); }
}

async function approve(index) {
  const item = state.localCandidates[index];
  if (!item || !item.job_id) { show("This capture has no originating job and cannot be approved.", "error"); return; }
  if (String(item.job_id) !== String(state.activeJobId || "")) { show("This candidate belongs to a different research job. Select its original job to approve it, or reject it.", "error"); return; }
  setBusy(true);
  try {
    const payload = OysterCaptureCore.approvalPayload(item);
    await api("/api/capture/approve", { method:"POST", body:JSON.stringify(payload) });
    state.localCandidates.splice(index, 1); await chrome.storage.local.set({ localCandidates:state.localCandidates });
    renderQueue(); show("Approved evidence was saved to the active research job.", "success");
  } catch (error) { show(error.message, "error"); }
  finally { setBusy(false); }
}

async function reject(index) { state.localCandidates.splice(index,1); await chrome.storage.local.set({localCandidates:state.localCandidates}); renderQueue(); }
async function clearAll() { if (!state.localCandidates.length || confirm("Discard every unapproved candidate?")) { state.localCandidates=[]; await chrome.storage.local.set({localCandidates:[]}); renderQueue(); } }
async function toggleStop() { state.collectionStopped=!state.collectionStopped; await chrome.storage.local.set({collectionStopped:state.collectionStopped}); renderStatus(); show(state.collectionStopped?"Collection stopped. Existing candidates remain available for review.":"Collection resumed.", "success"); }

function renderQueue() {
  $("#count").textContent = state.localCandidates.length;
  if (!state.localCandidates.length) { $("#queue").innerHTML='<p class="empty">No candidates yet. Select text and right-click, or scan the visible page.</p>'; return; }
  $("#queue").innerHTML = state.localCandidates.map((item,index)=>`<article class="card">
    <div class="meta"><strong>${escapeHtml(labelSource(item.source_type))}</strong> · ${escapeHtml(item.page_title || "Untitled page")}</div>
    <div class="meta">Brief: ${escapeHtml(item.job_title || `Job ${item.job_id || "unassigned"}`)}${item.research_question?`<br>Question: ${escapeHtml(item.research_question)}`:""}</div>
    <label for="excerpt-${index}">Excerpt or redaction</label><textarea id="excerpt-${index}" class="edit-excerpt" data-index="${index}">${escapeHtml(item.excerpt)}</textarea>
    <label class="toggle"><input type="checkbox" class="item-anonymize" data-index="${index}" ${item.anonymized?"checked":""}><span>Apply best-effort name redaction</span></label>
    <label for="note-${index}">Researcher note (optional)</label><textarea id="note-${index}" class="edit-note" data-index="${index}" placeholder="Why this matters">${escapeHtml(item.researcher_note||"")}</textarea>
    <div class="context"><a href="${escapeHtml(safeUrl(item.url))}" target="_blank" rel="noreferrer" title="Open source">${escapeHtml(item.url)}</a><span>Captured ${escapeHtml(formatTime(item.captured_at))}</span></div>
    <div class="actions"><button class="secondary approve" data-index="${index}">Approve & save</button><button class="danger reject" data-index="${index}">Reject</button></div></article>`).join("");
  document.querySelectorAll(".edit-excerpt").forEach(el=>el.onchange=()=>editItem(Number(el.dataset.index),"excerpt",el.value));
  document.querySelectorAll(".edit-note").forEach(el=>el.onchange=()=>editItem(Number(el.dataset.index),"researcher_note",el.value));
  document.querySelectorAll(".item-anonymize").forEach(el=>el.onchange=()=>toggleAnonymize(Number(el.dataset.index),el.checked));
  document.querySelectorAll(".approve").forEach(b=>b.onclick=()=>approve(Number(b.dataset.index)));
  document.querySelectorAll(".reject").forEach(b=>b.onclick=()=>reject(Number(b.dataset.index)));
}

async function editItem(index,key,value){ state.localCandidates[index][key]=value; await chrome.storage.local.set({localCandidates:state.localCandidates}); }
async function toggleAnonymize(index,enabled){
  const item=state.localCandidates[index];
  item.anonymized=enabled;
  item.excerpt=enabled ? OysterCaptureCore.anonymize(item.raw_excerpt || item.excerpt) : (item.raw_excerpt || item.excerpt);
  await chrome.storage.local.set({localCandidates:state.localCandidates}); renderQueue();
}

function renderStatus(connected) {
  const status=$("#connection");
  status.textContent = state.token ? (connected===false?"Unavailable":"Paired") : "Not paired";
  status.className=`pill ${state.token && connected!==false?"good":connected===false?"bad":"neutral"}`;
  $("#stop").textContent=state.collectionStopped?"Resume":"Stop";
  $("#scan").disabled=state.collectionStopped;
  chrome.tabs.query({active:true,currentWindow:true}).then(([tab])=>$("#source").textContent=labelSource(OysterCaptureCore.sourceType(tab?.url||"")));
}
function renderNotice(){ const n=state.notice; if(n&&Date.now()-n.at<120000) show(n.text,n.level); }
function show(text,level){ const n=$("#notice");n.hidden=false;n.textContent=text;n.className=`notice ${level||""}`; }
function setBusy(busy){ document.querySelectorAll("button,select").forEach(el=>el.disabled=busy || (el.id==="scan"&&state.collectionStopped)); }
function labelSource(type){ return ({discord_supervised:"Discord",x_supervised:"X",twitch_supervised:"Twitch",kick_supervised:"Kick",reddit_supervised:"Reddit",web_supervised:"Web"})[type]||"Web"; }
function escapeHtml(value){ const div=document.createElement("div");div.textContent=String(value??"");return div.innerHTML; }
function formatTime(value){ const date=new Date(value); return Number.isNaN(date.getTime())?String(value||""):date.toLocaleString(); }
function safeUrl(value){ try{const url=new URL(value);return ["http:","https:"].includes(url.protocol)?url.href:"#";}catch(_){return "#";} }
