"use strict";
const $=s=>document.querySelector(s);
document.addEventListener("DOMContentLoaded",async()=>{
  const s=await chrome.storage.local.get({baseUrl:"http://127.0.0.1:8765",token:"",anonymize:true,maxCandidates:25});
  $("#baseUrl").value=s.baseUrl; $("#anonymize").checked=s.anonymize; $("#maxCandidates").value=s.maxCandidates;
  $("#pair").onclick=pair; $("#unpair").onclick=unpair; $("#save").onclick=save;
  $("#trust").onclick=trust;
  loadTrusted();
});

async function api(path,options={}){
  const s=await chrome.storage.local.get({baseUrl:"http://127.0.0.1:8765",token:""});
  if(!s.token) throw new Error("Pair this browser first.");
  const base=OysterCaptureCore.normalizeBaseUrl(s.baseUrl);
  const r=await fetch(`${base}${path}`,{...options,headers:{"Content-Type":"application/json","Authorization":`Bearer ${s.token}`,...(options.headers||{})}});
  if(!r.ok){const b=await r.json().catch(()=>({}));throw new Error(b.error||b.detail||`Oyster returned ${r.status}.`);}
  return r.status===204?null:r.json();
}
async function loadTrusted(){
  const el=$("#trustedList");
  const s=await chrome.storage.local.get({token:""});
  if(!s.token){el.textContent="Pair the browser to manage trusted domains.";return;}
  try{
    const domains=await api("/api/capture/trusted-domains");
    if(!domains.length){el.innerHTML='<p class="muted">No trusted domains yet.</p>';return;}
    el.innerHTML=domains.map(d=>`<div class="row"><span>${escapeHtml(d.domain)}</span><button class="danger untrust" data-domain="${escapeHtml(d.domain)}">Remove</button></div>`).join("");
    el.querySelectorAll(".untrust").forEach(b=>b.onclick=()=>untrust(b.dataset.domain));
  }catch(e){el.innerHTML=`<p class="notice error">${escapeHtml(e.message)}</p>`;}
}
async function trust(){
  try{
    const domain=$("#trustDomain").value.trim().toLowerCase().replace(/^https?:\/\//,"").replace(/\/.*$/,"");
    if(!domain||!domain.includes(".")) throw new Error("Enter a bare domain such as discord.com.");
    // Chrome's own host-permission prompt is a second, independent consent for this domain.
    const granted=await chrome.permissions.request({origins:[`https://${domain}/*`,`https://*.${domain}/*`]});
    if(!granted) throw new Error("Domain permission was declined, so nothing will be captured.");
    await api("/api/capture/trusted-domains",{method:"POST",body:JSON.stringify({domain,approved_by_user:true})});
    $("#trustDomain").value="";notice(`Oyster can now capture ${domain} hands-free while you browse it.`,"success");
    loadTrusted();
  }catch(e){notice(e.message,"error");}
}
async function untrust(domain){
  try{
    await api(`/api/capture/trusted-domains/${encodeURIComponent(domain)}`,{method:"DELETE"});
    try{await chrome.permissions.remove({origins:[`https://${domain}/*`,`https://*.${domain}/*`]});}catch(_){}
    notice(`Stopped trusting ${domain}.`,"success");loadTrusted();
  }catch(e){notice(e.message,"error");}
}
function escapeHtml(v){const d=document.createElement("div");d.textContent=String(v==null?"":v);return d.innerHTML;}
async function pair(){
  try{
    const baseUrl=OysterCaptureCore.normalizeBaseUrl($("#baseUrl").value); const code=$("#code").value.trim();
    if(!code) throw new Error("Enter the one-time pairing code shown by Oyster.");
    const response=await fetch(`${baseUrl}/api/capture/pair`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({code,client_name:`Browser extension ${navigator.userAgent}`})});
    const data=await response.json().catch(()=>({})); if(!response.ok||!data.token) throw new Error(data.detail||data.error||"Pairing was rejected.");
    await chrome.storage.local.set({baseUrl,token:data.token}); $("#code").value=""; notice("Browser paired successfully.","success");
  }catch(error){notice(error.message,"error");}
}
async function unpair(){
  if(!confirm("Revoke this browser's Oyster access and discard its local token?"))return;
  const saved=await chrome.storage.local.get({baseUrl:"http://127.0.0.1:8765",token:""});
  try{
    if(saved.token){const base=OysterCaptureCore.normalizeBaseUrl(saved.baseUrl);const response=await fetch(`${base}/api/capture/revoke`,{method:"POST",headers:{"Content-Type":"application/json","Authorization":`Bearer ${saved.token}`},body:"{}"});if(!response.ok)throw new Error("Oyster could not revoke this browser. Start Oyster and try again.");}
    await chrome.storage.local.set({token:"",activeJobId:"",activeQuestion:"",localCandidates:[]});notice("Browser access revoked and local candidates cleared.","success");
  }catch(error){notice(error.message,"error");}
}
async function save(){ try{const baseUrl=OysterCaptureCore.normalizeBaseUrl($("#baseUrl").value);const maxCandidates=Math.min(Math.max(Number($("#maxCandidates").value)||25,1),25);await chrome.storage.local.set({baseUrl,anonymize:$("#anonymize").checked,maxCandidates});notice("Settings saved.","success");}catch(error){notice(error.message,"error");} }
function notice(text,level){const n=$("#notice");n.hidden=false;n.textContent=text;n.className=`notice ${level}`;}
