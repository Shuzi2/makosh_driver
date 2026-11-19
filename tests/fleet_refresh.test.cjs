const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const script=fs.readFileSync('static/app.js','utf8');
const source=script.slice(script.indexOf('const liveFleet='),script.indexOf('// A vehicle suggests'));
test('slow refresh keeps current cards visible and reports progress until response',async()=>{
  let resolveFetch,timer,interval;
  const note={dataset:{},textContent:''};
  const fleet={dataset:{fleetLiveUrl:'/?live=1'},innerHTML:'original cards',querySelector(){return {}},contains(){return false}};
  const document={visibilityState:'visible',activeElement:null,querySelector(s){return s==='[data-fleet-live-url]'?fleet:note},addEventListener(){}};
  const fetch=()=>new Promise(resolve=>{resolveFetch=resolve});
  vm.runInNewContext(source,{document,fetch,AbortSignal,setTimeout(fn){timer=fn;return 1},clearTimeout(){},setInterval(fn){interval=fn}});
  const run=interval();timer();
  assert.equal(note.dataset.state,'loading');assert.equal(fleet.innerHTML,'original cards');
  resolveFetch({ok:true,headers:{get:()=> 'application/json'},json:async()=>({cards:'new',metrics:'new'})});
  await run;assert.equal(note.dataset.state,'ready');assert.equal(note.textContent,'');assert.equal(fleet.innerHTML,'original cards');
});
