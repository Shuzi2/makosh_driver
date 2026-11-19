const test=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const source=fs.readFileSync('static/driver_drafts.js','utf8');
const flush=()=>new Promise(resolve=>setImmediate(resolve));
class Element{
  constructor(){this.children=[];this.handlers={};this.textContent='';this.hidden=false;}
  addEventListener(name,handler){this.handlers[name]=handler;}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(){this.children=[];}
  focus(){}
}
function fixture({confirm=true}={}){
  const row={key:'1:10:11',user:'1',trip:'10',stop:'11',kind:'weight',number:'',place:'Склад',revision:'old',updatedAt:1,frozen:true,files:[{blob:new Blob(['synthetic bytes'],{type:'application/pdf'}),type:'application/pdf',name:'weight.pdf'}]};
  const rows=new Map([[row.key,row],['other',{...row,key:'2:10:11',user:'2'}]]),calls=[];
  const store={list:async user=>[...rows.values()].filter(r=>r.user===user),get:async key=>rows.get(key),remove:async(key,revision)=>{calls.push({key,revision});if(rows.get(key)?.revision!==revision)throw new Error('conflict');rows.delete(key);}};
  const banner=new Element(),dialog=new Element(),title=new Element(),links=new Element(),parts=Object.fromEntries(['title','files','message','close','discard'].map(name=>[name,new Element()]));
  banner.querySelector=selector=>selector==='[data-photo-drafts-title]'?title:links;
  dialog.querySelector=selector=>parts[selector.slice(12,-1)];
  dialog.showModal=()=>{dialog.open=true;};dialog.close=()=>{dialog.open=false;dialog.handlers.close();};
  const events=[],window={DriverUploadStore:store,addEventListener(){},confirm:()=>confirm,dispatchEvent:event=>events.push(event)};
  const document={body:{dataset:{driverUser:'1'}},createElement:()=>new Element(),querySelector:selector=>selector==='[data-photo-drafts]'?banner:selector==='[data-photo-draft-viewer]'?dialog:null};
  class CustomEvent extends Event{constructor(name,options){super(name);this.detail=options.detail;}}
  vm.runInNewContext(source,{window,document,URL,URLSearchParams,Event,CustomEvent});
  return {row,rows,calls,events,banner,dialog,title,links,parts,open:async()=>{await flush();await links.children[0].children[1].handlers.click();await flush();}};
}
test('draft notice contains only this signed-in driver’s local files',async()=>{
  const app=fixture();await flush();assert.equal(app.links.children.length,1);assert.match(app.title.textContent,/: 1$/);
  assert.match(app.links.children[0].children[0].href,/trip=10&stop=11&kind=weight/);
});
test('local copy remains accessible without requesting the old trip from the server',async()=>{
  const app=fixture();await app.open();assert.equal(app.dialog.open,true);
  const link=app.parts.files.children[0];assert.match(link.href,/^blob:/);assert.equal(link.download,'weight.pdf');assert.match(link.children[0].textContent,/Зберегти копію/);
  app.dialog.close();assert.equal(app.parts.files.children.length,0);
});
test('discard requires the driver’s explicit confirmation',async()=>{
  const app=fixture({confirm:false});await app.open();await app.parts.discard.handlers.click();assert.equal(app.calls.length,0);assert.equal(app.dialog.open,true);
});
test('discard cannot remove a newer version created in another tab',async()=>{
  const app=fixture();await app.open();app.rows.set(app.row.key,{...app.row,revision:'newer'});
  await app.parts.discard.handlers.click();assert.equal(app.rows.get(app.row.key).revision,'newer');assert.match(app.parts.message.textContent,/вже змінилася/);assert.equal(app.dialog.open,true);
});
test('confirmed discard uses the exact revision and tells the matching upload form to clear',async()=>{
  const app=fixture();await app.open();await app.parts.discard.handlers.click();assert.equal(app.rows.has(app.row.key),false);assert.equal(app.rows.has('other'),true);
  assert.deepEqual(app.calls,[{key:app.row.key,revision:'old'}]);assert.equal(app.events.find(event=>event.type==='driver:draft-discard').detail.key,app.row.key);assert.equal(app.dialog.open,false);
});
