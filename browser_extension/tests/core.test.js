"use strict";
const assert=require("node:assert/strict");
const test=require("node:test");
const core=require("../core.js");

test("recognizes supported platform adapters",()=>{
  assert.equal(core.sourceType("https://discord.com/channels/1/2"),"discord_supervised");
  assert.equal(core.sourceType("https://x.com/example/status/1"),"x_supervised");
  assert.equal(core.sourceType("https://www.twitch.tv/foo"),"twitch_supervised");
  assert.equal(core.sourceType("https://kick.com/example"),"kick_supervised");
  assert.equal(core.sourceType("https://old.reddit.com/r/foo"),"reddit_supervised");
  assert.equal(core.sourceType("https://example.com"),"web_supervised");
});

test("allows local APIs only",()=>{
  assert.equal(core.normalizeBaseUrl("http://127.0.0.1:8765/path"),"http://127.0.0.1:8765");
  assert.throws(()=>core.normalizeBaseUrl("https://example.com"),/local/);
  assert.throws(()=>core.normalizeBaseUrl("https://localhost:8765"),/local/);
});

test("capture defaults to anonymized and unapproved",()=>{
  const item=core.capture({excerpt:"@Alice: loves glass bottles",url:"https://discord.com/channels/1/2",page_title:"Chat"});
  assert.equal(item.excerpt,"Participant 1: loves glass bottles");
  assert.equal(item.raw_excerpt,"@Alice: loves glass bottles");
  assert.equal(item.anonymized,true);
  assert.equal(item.approved_by_user,false);
});

test("scores mission terms case-insensitively",()=>assert.equal(core.score("Holiday Costco water",["holiday","WATER","missing"]),2));

test("canonical URL removes fragments and tracking but preserves meaningful query",()=>{
  assert.equal(core.canonicalUrl("https://example.com/post?id=7&utm_source=x#reply"),"https://example.com/post?id=7");
});

test("same URL can retain distinct excerpts",()=>{
  const first={url:"https://example.com/post",excerpt:"First observation"};
  const second={url:"https://example.com/post",excerpt:"Second observation"};
  assert.equal(core.dedupeCaptures([], [first,second],25).length,2);
});

test("dedupe rejects normalized duplicates within one batch",()=>{
  const a={url:"https://example.com/post?utm_source=x",excerpt:"Holiday   hosting"};
  const b={url:"https://example.com/post#thread",excerpt:" holiday hosting "};
  assert.equal(core.dedupeCaptures([], [a,b],25).length,1);
});

test("capture binds originating job metadata",()=>{
  const item=core.capture({excerpt:"Evidence",url:"https://example.com",job_id:"42",job_title:"Kirkland 2026",research_question:"What matters?"});
  assert.equal(item.job_id,"42"); assert.equal(item.job_title,"Kirkland 2026"); assert.equal(item.research_question,"What matters?");
});

test("atomic approval preserves job and id but excludes raw text when anonymized",()=>{
  const item=core.capture({excerpt:"@Alice: Evidence",url:"https://discord.com/channels/1/2",job_id:"42",job_title:"Kirkland"});
  const payload=core.approvalPayload(item);
  assert.equal(payload.job_id,"42"); assert.equal(payload.client_capture_id,item.client_capture_id);
  assert.equal(payload.approved_by_user,true); assert.equal("raw_excerpt" in payload,false);
});

test("atomic approval rejects missing immutable binding",()=>{
  assert.throws(()=>core.approvalPayload({client_capture_id:"x",excerpt:"Evidence"}),/original job/);
});

test("redactUrl strips secret-looking query values but keeps the rest",()=>{
  assert.equal(core.redactUrl("https://discord.com/api?token=abc&channel=42"),"https://discord.com/api?token=%5BREDACTED%5D&channel=42");
  assert.equal(core.redactUrl("https://discord.com/api?channel=42"),"https://discord.com/api?channel=42");
});

test("domainMatches accepts exact host and subdomains only",()=>{
  assert.equal(core.domainMatches("discord.com","discord.com"),true);
  assert.equal(core.domainMatches("gateway.discord.com","discord.com"),true);
  assert.equal(core.domainMatches("evildiscord.com","discord.com"),false);
  assert.equal(core.domainMatches("discord.com.evil.com","discord.com"),false);
});

test("extractMessages pulls message text with authors from nested JSON",()=>{
  const discordShape=[{content:"great movie",author:{username:"bob"}},{content:"loved the ending",author:{username:"amy"}}];
  const out=core.extractMessages(discordShape,10,4000);
  assert.match(out,/bob: great movie/);
  assert.match(out,/amy: loved the ending/);
});

test("extractMessages honors the item cap",()=>{
  const many=Array.from({length:50},(_,i)=>({text:`msg ${i}`,name:`u${i}`}));
  assert.equal(core.extractMessages(many,5,4000).split("\n").length,5);
});

test("payloadFingerprint is stable and dedupes identical payloads",()=>{
  const a=core.payloadFingerprint("https://x.com/api","BODY");
  const b=core.payloadFingerprint("https://x.com/api","BODY");
  const c=core.payloadFingerprint("https://x.com/api","OTHER");
  assert.equal(a,b);
  assert.notEqual(a,c);
});

test("capture passes through metadata and approved_session mode",()=>{
  const item=core.capture({excerpt:"bob: hi",url:"https://discord.com/x",job_id:"5",capture_mode:"approved_session",metadata:{session_id:9,network:{url:"https://discord.com/api"}}});
  assert.equal(item.capture_mode,"approved_session");
  assert.equal(item.metadata.session_id,9);
});
