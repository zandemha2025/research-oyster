"use strict";
const $=s=>document.querySelector(s);
document.addEventListener("DOMContentLoaded",async()=>{
  const s=await chrome.storage.local.get({baseUrl:"http://127.0.0.1:8765",token:"",anonymize:true,maxCandidates:25});
  $("#baseUrl").value=s.baseUrl; $("#anonymize").checked=s.anonymize; $("#maxCandidates").value=s.maxCandidates;
  $("#pair").onclick=pair; $("#unpair").onclick=unpair; $("#save").onclick=save;
});
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
