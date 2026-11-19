const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('static/driver_updates.js','utf8');
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function fixture(fetchImpl){
  const dashboard={dataset:{version:'old',updatesUrl:'/driver/updates/'}};
  const note={textContent:''};
  const warning={hidden:true};
  const intervals=[];
  const window={driverHasPendingAction:false,events:{},location:{pathname:'/',reloads:0,reload(){this.reloads++;},replace(){this.reloads++;}},addEventListener(name,handler){this.events[name]=handler;}};
  const document={visibilityState:'visible',dirty:false,events:{},querySelector(selector){if(selector==='[data-driver-dashboard]')return dashboard;if(selector==='[data-driver-live-note]')return note;if(selector==='[data-driver-cancel-warning]')return warning;return this.dirty?{}:null;},addEventListener(name,handler){this.events[name]=handler;}};
  const context={window,document,AbortController,setTimeout,clearTimeout,fetch:fetchImpl,setInterval(handler,delay){intervals.push({handler,delay});}};
  vm.runInNewContext(source,context);
  return {window,document,note,warning,dashboard,intervals,check:intervals[0].handler};
}
function response(version,cancellation_pending=false){return {ok:true,redirected:false,headers:{get:()=> 'application/json'},json:async()=>({version,cancellation_pending})};}
test('empty driver screen checks every five seconds and refreshes only when server data changes',async()=>{
  let version='old';const app=fixture(async()=>response(version));
  assert.equal(app.intervals[0].delay,5000);
  await app.check();assert.equal(app.window.location.reloads,0);
  version='new';await app.check();assert.equal(app.window.location.reloads,1);
});
test('drafts and unconfirmed status actions cannot be interrupted by updates',async()=>{
  let calls=0;const app=fixture(async()=>{calls++;return response('new');});
  app.document.dirty=true;await app.check();assert.equal(calls,1);assert.equal(app.window.location.reloads,0);
  app.document.dirty=false;app.window.driverHasPendingAction=true;await app.check();assert.equal(calls,2);assert.equal(app.window.location.reloads,0);
  app.window.driverHasPendingAction=false;await app.check();assert.equal(app.window.location.reloads,1);
});
test('new assignment is announced without discarding a document draft',async()=>{
  const app=fixture(async()=>({ok:true,redirected:false,headers:{get:()=> 'application/json'},json:async()=>({version:'new',assignment_pending:true})}));
  app.dashboard.dataset.cancellationsOnly='1';app.document.dirty=true;
  await app.check();assert.equal(app.window.location.reloads,0);assert.match(app.note.textContent,/нове завдання/i);
});

test('cancellation warning remains visible without losing a draft or pending action',async()=>{
  const app=fixture(async()=>response('new',true));
  app.document.dirty=true;await app.check();assert.equal(app.warning.hidden,false);assert.equal(app.window.location.reloads,0);
  app.document.dirty=false;app.window.driverHasPendingAction=true;await app.check();assert.equal(app.warning.hidden,false);assert.equal(app.window.location.reloads,0);
  app.window.driverHasPendingAction=false;await app.check();assert.equal(app.window.location.reloads,1);
});

test('documents page refreshes changed documents without discarding a draft',async()=>{
  let cancelled=false;const app=fixture(async()=>response(cancelled?'old':'new',cancelled));
  app.dashboard.dataset.cancellationsOnly='1';app.window.location.pathname='/driver/documents/';
  app.document.dirty=true;await app.check();assert.equal(app.window.location.reloads,0);
  assert.match(app.note.textContent,/онов/);
  app.document.dirty=false;await app.check();assert.equal(app.window.location.reloads,1);
});
test('typed TTN number and changed document type prevent automatic refresh',async()=>{
  const app=fixture(async()=>response('new'));
  app.dashboard.dataset.cancellationsOnly='1';app.window.location.pathname='/driver/documents/';
  const original=app.document.querySelector.bind(app.document);
  app.document.querySelector=selector=>selector.includes('input[name="ttn_number"]')?{value:'123',defaultValue:''}:original(selector);
  await app.check();assert.equal(app.window.location.reloads,0);
  app.document.querySelector=original;
  app.document.querySelectorAll=()=>[{value:'signed_ttn',options:[{value:'ttn',defaultSelected:true},{value:'signed_ttn',defaultSelected:false}]}];
  await app.check();assert.equal(app.window.location.reloads,0);
});
test('documents page opens cancellation notices even when its initial version already includes them',async()=>{
  let cancelled=false;const app=fixture(async()=>response('old',cancelled));
  app.dashboard.dataset.cancellationsOnly='1';app.window.location.pathname='/driver/documents/';
  await app.check();assert.equal(app.window.location.reloads,0);
  cancelled=true;await app.check();assert.equal(app.window.location.reloads,1);
});
test('typing during an in-flight refresh prevents discarding the draft',async()=>{
  let resolve;const app=fixture(()=>new Promise(done=>resolve=done));
  const check=app.check();app.document.dirty=true;resolve(response('new'));await check;
  assert.equal(app.window.location.reloads,0);
});
test('failed connection keeps the screen and reconnect checks immediately',async()=>{
  let offline=true;const app=fixture(async()=>{if(offline)throw new Error('offline');return response('new');});
  await app.check();assert.equal(app.window.location.reloads,0);assert.match(app.note.textContent,/немає/);
  offline=false;app.window.events.online();await flush();assert.equal(app.window.location.reloads,1);
});
