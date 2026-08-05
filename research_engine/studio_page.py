"""The Studio single-page UI. Kept separate from studio.py behavior. Token-injected."""

PAGE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Research Oyster Studio</title><style>
:root{--ink:#18211d;--muted:#66716b;--paper:#f6f4ee;--card:#fff;--green:#26734d;--red:#a53b32;--line:#dedfd9;--accent:#173f35;--wash:#eef2f0}
*{box-sizing:border-box}html,body{height:100%}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
.app{display:grid;grid-template-columns:220px 1fr 380px;height:100vh}
.col{height:100vh;overflow:auto;display:flex;flex-direction:column}
.side{background:var(--wash);border-right:1px solid var(--line);padding:16px}
.side h1{font:700 20px/1 Georgia,serif;letter-spacing:-.5px;margin:0 0 4px}
.side .sub{color:var(--muted);font-size:12px;margin-bottom:16px}
.newbtn{width:100%;font:inherit;font-weight:650;border:0;border-radius:9px;padding:10px;background:var(--accent);color:#fff;cursor:pointer;margin-bottom:14px}
.convo{padding:9px 10px;border-radius:8px;cursor:pointer;font-size:13px;border:1px solid transparent}
.convo:hover{background:#e2e9e5}.convo.active{background:#fff;border-color:var(--line);font-weight:600}
.convo small{display:block;color:var(--muted);font-weight:400}
.chat{background:var(--paper)}
.stream{flex:1;overflow:auto;padding:24px 26px}
.msg{max-width:760px;margin:0 0 16px}
.msg.user{margin-left:auto;background:var(--accent);color:#fff;padding:11px 15px;border-radius:14px 14px 3px 14px;width:fit-content;max-width:70%}
.msg.assistant{white-space:pre-wrap}
.msg.assistant b.who,.msg.err b.who{display:block;font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:var(--muted);margin-bottom:3px}
.msg.err{color:var(--red)}
.think{border-left:3px solid #c9b98a;background:#fbf7ec;color:#6b5f3d;padding:8px 12px;margin:0 0 14px;border-radius:0 8px 8px 0;font-size:13px;white-space:pre-wrap}
.think .lbl{font-weight:650;font-size:11px;text-transform:uppercase;letter-spacing:.5px;opacity:.8}
.think .tbody{display:block;margin-top:4px}
.composer{border-top:1px solid var(--line);padding:14px 18px;background:var(--card);display:flex;gap:10px}
.composer textarea{flex:1;resize:none;font:inherit;padding:11px 13px;border:1px solid #bfc5c1;border-radius:12px;max-height:140px}
.composer button{font:inherit;font-weight:650;border:0;border-radius:12px;padding:0 20px;background:var(--accent);color:#fff;cursor:pointer}
.composer button:disabled{opacity:.5;cursor:default}
.activity{background:var(--card);border-left:1px solid var(--line);padding:0}
.actitle{position:sticky;top:0;background:var(--card);border-bottom:1px solid var(--line);padding:13px 16px;font-weight:700;font-size:13px;display:flex;justify-content:space-between;align-items:center}
.tabs{display:flex;gap:6px}
.tab{font:inherit;font-size:12px;font-weight:600;border:1px solid var(--line);background:var(--wash);border-radius:7px;padding:4px 10px;cursor:pointer}
.tab.active{background:var(--accent);color:#fff;border-color:var(--accent)}
.feed{padding:12px 14px}
.evt{border:1px solid var(--line);border-radius:9px;margin-bottom:9px;overflow:hidden;font-size:12.5px}
.evt .head{padding:8px 11px;cursor:pointer;display:flex;gap:8px;align-items:center}
.evt.call .head{background:#eef3ff}.evt.result .head{background:#eef7f0}.evt.result.err .head{background:#fdecea}
.evt .ico{font-weight:700}
.evt .nm{font-weight:650}
.evt .body{display:none;border-top:1px solid var(--line);background:#fbfbfa;padding:9px 11px}
.evt.open .body{display:block}
.evt pre{margin:0;white-space:pre-wrap;word-break:break-word;font:11.5px/1.45 ui-monospace,Menlo,Consolas,monospace;max-height:280px;overflow:auto}
.muted{color:var(--muted)}
.reportwrap{height:100%;display:flex;flex-direction:column}
.reportwrap select{margin:12px 14px;font:inherit;padding:7px;border:1px solid var(--line);border-radius:8px}
.reportwrap iframe{flex:1;border:0;width:100%;background:#fff}
.empty{color:var(--muted);padding:40px 16px;text-align:center;font-size:13px}
.pill{font-size:11px;border-radius:20px;padding:2px 9px;background:var(--wash);color:var(--muted)}
.pill.ok{background:#e7f3ec;color:var(--green)}
</style></head><body>
<div class="app">
  <div class="col side">
    <h1>Studio</h1><div class="sub" id="authpill">Research Oyster</div>
    <button class="newbtn" onclick="newConversation()">+ New research</button>
    <div id="convos"></div>
  </div>

  <div class="col chat">
    <div class="stream" id="stream"><div class="empty">Start a new research conversation, then watch the agent work — every tool call and raw result shows on the right, nothing hidden.</div></div>
    <div class="composer">
      <textarea id="input" rows="1" placeholder="Ask Oyster to research something…" disabled></textarea>
      <button id="sendbtn" onclick="send()" disabled>Send</button>
    </div>
  </div>

  <div class="col activity">
    <div class="actitle"><span>Live activity</span>
      <div class="tabs"><button class="tab active" id="tabFeed" onclick="showTab('feed')">Activity</button><button class="tab" id="tabReport" onclick="showTab('report')">Report</button></div>
    </div>
    <div class="feed" id="feed"><div class="empty">Tool calls and raw results will appear here as the agent works.</div></div>
    <div class="reportwrap" id="reportPane" style="display:none">
      <select id="reportSelect" onchange="loadReport(this.value)"><option value="">No report yet</option></select>
      <iframe id="reportFrame"></iframe>
    </div>
  </div>
</div>
<script>
const token='__TOKEN__';
let active=null, es=null, curAssistant=null, curThinking=null;
const convs={};

async function api(path,body){const opt=body?{method:'POST',headers:{'Content-Type':'application/json','X-Studio-Token':token},body:JSON.stringify(body)}:{};const r=await fetch(path,opt);const d=await r.json();if(!r.ok)throw new Error(d.error||'request failed');return d}
function el(t,c,txt){const e=document.createElement(t);if(c)e.className=c;if(txt!=null)e.textContent=txt;return e}
function esc(s){const d=document.createElement('div');d.textContent=s==null?'':String(s);return d.innerHTML}

async function boot(){try{const h=await api('/api/health');document.getElementById('authpill').innerHTML=`auth: <span class="pill ${h.auth==='ambient'?'ok':'ok'}">${h.auth}</span> · db: <span class="pill ${h.db?'ok':''}">${h.db?'ok':'down'}</span>`}catch(e){}await refreshConvos()}
async function refreshConvos(){const list=await api('/api/conversations');const box=document.getElementById('convos');box.innerHTML='';for(const c of list){convs[c.id]=convs[c.id]||c;const d=el('div','convo'+(c.id===active?' active':''));d.innerHTML=`${esc(c.title)}<small>${c.job_ids.length?c.job_ids.length+' report(s)':'no report yet'}</small>`;d.onclick=()=>openConversation(c.id);box.appendChild(d)}}

async function newConversation(){const d=await api('/api/conversations',{title:'New research'});convs[d.conversation_id]={id:d.conversation_id,title:d.title,job_ids:[]};await refreshConvos();openConversation(d.conversation_id)}

function openConversation(cid){active=cid;curAssistant=null;
  document.getElementById('stream').innerHTML='';
  document.getElementById('feed').innerHTML='';
  rebuildReportSelect();
  document.getElementById('input').disabled=false;document.getElementById('sendbtn').disabled=false;
  document.getElementById('input').focus();
  refreshConvos();
  if(es)es.close();
  es=new EventSource('/api/chat/stream?conversation_id='+cid);
  const handlers=['user','text','thinking','thinking_start','tool_call','tool_result','job_linked','result','done','error','ping'];
  handlers.forEach(t=>es.addEventListener(t,ev=>onEvent(t,JSON.parse(ev.data))));
}

function streamBox(){return document.getElementById('stream')}
function scrollStream(){const s=streamBox();s.scrollTop=s.scrollHeight}
function feedBox(){return document.getElementById('feed')}

function onEvent(type,data){
  if(type==='ping')return;
  if(type==='user'){curAssistant=null;curThinking=null;const m=el('div','msg user',data.text);streamBox().appendChild(m);scrollStream();return}
  if(type==='text'){curThinking=null;if(!curAssistant){curAssistant=el('div','msg assistant');curAssistant.innerHTML='<b class="who">Oyster</b>';const span=el('span','body-text');curAssistant.appendChild(span);streamBox().appendChild(curAssistant)}curAssistant.querySelector('.body-text').textContent+=data.text;scrollStream();return}
  if(type==='thinking_start'){curAssistant=null;curThinking=el('div','think');curThinking.innerHTML='<span class="lbl">thinking</span>';const s=el('span','tbody');curThinking.appendChild(s);streamBox().appendChild(curThinking);scrollStream();return}
  if(type==='thinking'){curAssistant=null;if(!curThinking){curThinking=el('div','think');curThinking.innerHTML='<span class="lbl">thinking</span>';const s=el('span','tbody');curThinking.appendChild(s);streamBox().appendChild(curThinking)}curThinking.querySelector('.tbody').textContent+=data.text;scrollStream();return}
  if(type==='tool_call'){curThinking=null;addEvt('call','→',data.name,JSON.stringify(data.input,null,2));return}
  if(type==='tool_result'){addEvt('result'+(data.is_error?' err':''),data.is_error?'✕':'✓',data.name||'result',data.content);if(/export_research_report|write_research_synthesis/.test(data.name||'')){const c=convs[active];if(c&&c.job_ids&&c.job_ids.length)setTimeout(()=>rebuildReportSelect(c.job_ids[c.job_ids.length-1]),600)}return}
  if(type==='job_linked'){const c=convs[active];if(c&&!c.job_ids.includes(data.job_id)){c.job_ids.push(data.job_id);rebuildReportSelect(data.job_id)}refreshConvos();return}
  if(type==='result'){curAssistant=null;if(data.cost_usd!=null){const f=el('div','msg muted','— turn complete · $'+Number(data.cost_usd).toFixed(4));streamBox().appendChild(f)}return}
  if(type==='error'){curAssistant=null;const m=el('div','msg err');m.innerHTML='<b class="who">error</b>'+esc(data.message);streamBox().appendChild(m);scrollStream();return}
  if(type==='done'){document.getElementById('input').disabled=false;document.getElementById('sendbtn').disabled=false;const c=convs[active];if(c&&c.job_ids&&c.job_ids.length)rebuildReportSelect(c.job_ids[c.job_ids.length-1]);return}
}

function addEvt(cls,ico,name,body){const f=feedBox();if(f.querySelector('.empty'))f.innerHTML='';
  const e=el('div','evt '+cls);
  const head=el('div','head');head.innerHTML=`<span class="ico">${ico}</span><span class="nm">${esc(name)}</span>`;
  head.onclick=()=>e.classList.toggle('open');
  const b=el('div','body');const pre=el('pre',null,body);b.appendChild(pre);
  e.appendChild(head);e.appendChild(b);f.appendChild(e);f.scrollTop=f.scrollHeight;
}

function rebuildReportSelect(select){const c=convs[active];const sel=document.getElementById('reportSelect');sel.innerHTML='';
  if(!c||!c.job_ids.length){sel.innerHTML='<option value="">No report yet</option>';document.getElementById('reportFrame').src='about:blank';return}
  c.job_ids.forEach(j=>{const o=el('option',null,'Report — job #'+j);o.value=j;sel.appendChild(o)});
  const pick=select||c.job_ids[c.job_ids.length-1];sel.value=pick;loadReport(pick);
}
function loadReport(j){if(j)document.getElementById('reportFrame').src='/api/report/'+j+'?_='+Date.now()}

function showTab(which){document.getElementById('tabFeed').classList.toggle('active',which==='feed');document.getElementById('tabReport').classList.toggle('active',which==='report');document.getElementById('feed').style.display=which==='feed'?'block':'none';document.getElementById('reportPane').style.display=which==='report'?'flex':'none'}

async function send(){const inp=document.getElementById('input');const msg=inp.value.trim();if(!msg||!active)return;inp.value='';inp.disabled=true;document.getElementById('sendbtn').disabled=true;try{await api('/api/chat/send',{conversation_id:active,message:msg})}catch(e){onEvent('error',{message:e.message});inp.disabled=false;document.getElementById('sendbtn').disabled=false}}

document.getElementById('input').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send()}});
document.getElementById('input').addEventListener('input',function(){this.style.height='auto';this.style.height=Math.min(this.scrollHeight,140)+'px'});
boot();
</script></body></html>'''
