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
