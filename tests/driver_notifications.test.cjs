const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('static/driver_notifications.js','utf8');
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function fixture(fetchImpl,active=false,serviceActive=false){
  const badge={hidden:true,textContent:''},list={children:[],replaceChildren(...items){this.children=items;}};
  const tripBadge={hidden:true,textContent:'',setAttribute(){}},handlers={};
  const tripLink={getAttribute(){return active?'page':null},addEventListener(name,fn){handlers[name]=fn}};
  const serviceBadge={hidden:true,textContent:'',setAttribute(){}},serviceHandlers={};
  const serviceLink={getAttribute(){return serviceActive?'page':null},addEventListener(name,fn){serviceHandlers[name]=fn}};
  const box={dataset:{url:'/driver/notifications/',readUrl:'/driver/notifications/read/'},querySelector(s){return s.includes('count')?badge:list},addEventListener(){}};
  const intervals=[];
  const document={body:{dataset:{driverUser:'1'}},visibilityState:'visible',querySelector(s){return s==='[data-driver-notifications]'?box:s==='[data-driver-trip-link]'?tripLink:s==='[data-driver-trip-count]'?tripBadge:s==='[data-driver-service-link]'?serviceLink:s==='[data-driver-service-count]'?serviceBadge:null},createElement(tag){return {tag,children:[],setAttribute(){},append(...x){this.children.push(...x)},addEventListener(){}}},createDocumentFragment(){return {children:[],append(x){this.children.push(x)}}},addEventListener(){}};
  vm.runInNewContext(source,{document,window:{location:{}},fetch:fetchImpl,AbortSignal,setInterval(fn){intervals.push(fn)}});
  return {badge,list,tripBadge,serviceBadge,openTrip:()=>handlers.click(),openService:()=>serviceHandlers.click(),check:()=>intervals[0]()};
}
test('service reply appears in service badge and bell without marking trip',async()=>{
  const app=fixture(async()=>({ok:true,headers:{get:()=> 'application/json'},json:async()=>({unread:1,items:[{id:'service:2',type:'service',title:'Пічка',time:'',url:'/driver/service/',read:false}]})}));
  await flush();assert.equal(app.serviceBadge.textContent,'1');assert.equal(app.serviceBadge.hidden,false);assert.equal(app.tripBadge.hidden,true);
  app.openService();assert.equal(app.serviceBadge.hidden,true);assert.equal(app.badge.hidden,false);
});
test('service badge is hidden while on service page',async()=>{
  const app=fixture(async()=>({ok:true,headers:{get:()=> 'application/json'},json:async()=>({unread:1,items:[{id:'service:2',type:'service',title:'Пічка',time:'',url:'/driver/service/',read:false}]})}),false,true);
  await flush();assert.equal(app.serviceBadge.hidden,true);assert.equal(app.badge.hidden,false);
});
test('trip badge clears on opening without clearing the bell',async()=>{
  const app=fixture(async()=>({ok:true,headers:{get:()=> 'application/json'},json:async()=>({unread:1,items:[{id:4,trip:5,vehicle:'A',time:'',url:'/',read:false}]})}));
  await flush();assert.equal(app.tripBadge.textContent,'1');assert.equal(app.tripBadge.hidden,false);
  app.openTrip();assert.equal(app.tripBadge.hidden,true);assert.equal(app.badge.hidden,false);
  await app.check();assert.equal(app.tripBadge.hidden,true);
});
test('trip badge stays hidden while viewing my trips but bell remains visible',async()=>{
  const app=fixture(async()=>({ok:true,headers:{get:()=> 'application/json'},json:async()=>({unread:1,items:[{id:4,trip:5,vehicle:'A',time:'',url:'/',read:false}]})}),true);
  await flush();assert.equal(app.tripBadge.hidden,true);assert.equal(app.badge.hidden,false);
});
test('notification errors are visible and recover on the next poll',async()=>{
  let offline=true;const app=fixture(async()=>{if(offline)throw Error('offline');return {ok:true,headers:{get:()=> 'application/json'},json:async()=>({unread:0,items:[]})}});
  await flush();assert.match(app.list.children[0].textContent,/Не вдалося/);
  offline=false;await app.check();assert.match(app.list.children[0].children[0].textContent,/немає/);
});
