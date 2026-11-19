const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/resource_metrics.js', 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));

function fixture(fetch) {
  let next = 0;
  const timers = new Map();
  const status = {textContent:'', classList:{add(){},remove(){}}};
  const value = {textContent:''}, hint = {textContent:''}, progress = {};
  const card = {dataset:{resourceKey:'cpu'},querySelector(s){return s==='[data-resource-value]'?value:s==='[data-resource-hint]'?hint:progress;}};
  const panel = {dataset:{resourceUrl:'/administration/metrics/'},querySelector(){return status;},querySelectorAll(){return [card];}};
  const document = {visibilityState:'visible',querySelector(){return panel;},addEventListener(){}};
  const window = {setTimeout(fn,delay){const id=++next;timers.set(id,{fn,delay});return id;},clearTimeout(id){timers.delete(id);}};
  vm.runInNewContext(source, {document,window,fetch,AbortController,Intl,Date,Array,Number,Math});
  async function tick(delay) {
    const match = [...timers.entries()].find(([,v])=>v.delay===delay);
    assert.ok(match, 'timer '+delay);
    timers.delete(match[0]); match[1].fn(); await flush();
  }
  return {status,value,progress,tick};
}

test('updates values and uses the server measurement time', async()=>{
  const app=fixture(async()=>({ok:true,json:async()=>({sampled_at:'2026-11-09T08:00:00Z',rows:[{key:'cpu',value:'12.5 %',hint:'ok',percent:12.5}]})}));
  await app.tick(2250);
  assert.equal(app.value.textContent,'12.5 %');
  assert.equal(app.progress.value,12.5);
  assert.match(app.status.textContent,/10:00:00/);
});

test('a stalled request times out and a later request can recover', async()=>{
  let calls=0;
  const app=fixture((url,options)=>{
    calls++;
    if(calls===1) return new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new Error('aborted'))));
    return Promise.resolve({ok:true,json:async()=>({sampled_at:'2026-11-09T08:00:03Z',rows:[{key:'cpu',value:'8 %',hint:'ok',percent:8}]})});
  });
  await app.tick(2250); await app.tick(5000);
  assert.match(app.status.textContent,/повторюємо/);
  await app.tick(2250);
  assert.equal(app.value.textContent,'8 %');
  assert.equal(calls,2);
});
