const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
function fixture(){
  let version='old',open=false,reloads=0,tick,messagesHtml='';
  const note={hidden:true,textContent:''},handlers={};
  const messages={innerHTML:''};
  const page={dataset:{tripLiveUrl:'/trips/1/?live=1',tripVersion:'old'},querySelector:s=>s==='[data-driver-messages]'?messages:note,addEventListener:(key,fn)=>handlers[key]=fn};
  const document={visibilityState:'visible',querySelector:s=>s==='[data-trip-live-url]'?page:s==='dialog[open]'&&open?{}:null,addEventListener(){}};
  vm.runInNewContext(fs.readFileSync('static/trip_detail.js','utf8'),{document,window:{location:{reload(){reloads++;}},addEventListener(){}},AbortSignal:{timeout(){}},setInterval:fn=>tick=fn,fetch:async()=>({ok:true,headers:{get:()=> 'application/json'},json:async()=>({version,driver_messages_html:messagesHtml})})});
  return {note,messages,handlers,tick:()=>tick(),change:()=>version='new',setMessages:value=>messagesHtml=value,open:value=>open=value,reloads:()=>reloads};
}
test('new driver messages update without replacing an unsaved form',async()=>{
  const app=fixture();app.handlers.input({target:{closest:()=>({})}});
  app.setMessages('<p>Затримка</p>');await app.tick();
  assert.equal(app.messages.innerHTML,'<p>Затримка</p>');assert.equal(app.reloads(),0);
});
test('trip detail refreshes only when server version changes',async()=>{
  const app=fixture();await app.tick();assert.equal(app.reloads(),0);
  app.change();await app.tick();assert.equal(app.reloads(),1);
});
test('trip detail never replaces unsaved weights or selected upload files',async()=>{
  for(const event of ['input','change']){
    const app=fixture();app.handlers[event]({target:{closest:()=>({})}});
    app.change();await app.tick();assert.equal(app.reloads(),0);assert.equal(app.note.hidden,false);
    assert.match(app.note.textContent,/Завершіть редагування/);
  }
});
test('closing an unmodified editor allows the current driver stage to refresh',async()=>{
  const app=fixture();app.open(true);app.change();await app.tick();assert.equal(app.reloads(),0);
  app.open(false);await app.tick();assert.equal(app.reloads(),1);
});
