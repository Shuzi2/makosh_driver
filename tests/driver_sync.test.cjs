const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('static/driver.js','utf8')+'\n'+fs.readFileSync('static/driver_uploads.js','utf8');
class Element {
  constructor(){this.dataset={};this.handlers={};this.disabled=false;this.hidden=true;this.textContent='';this.attrs={};}
  addEventListener(name,handler){this.handlers[name]=handler;}
  setAttribute(name,value){this.attrs[name]=value;}
  removeAttribute(name){delete this.attrs[name];}
  replaceChildren(){this.children=[];}
  append(...items){this.children=(this.children||[]).concat(items);}
  hasAttribute(name){return name==='data-original-disabled'?this.dataset.originalDisabled!==undefined:name in this.attrs;}
}
function fixture({storage=new Map(),user='1',fetchImpl,upload=false,draftStore}={}){
  const text=new Element(),retry=new Element(),reload=new Element(),banner=new Element(),button=new Element(),form=new Element();
  form.dataset={syncUrl:'/driver/trips/10/action/',revision:'2026-10-22T10:00:00+00:00'};
  form.querySelectorAll=()=>[button];form.fields={action:'status',status:'loading'};
  banner.querySelector=selector=>({'[data-driver-sync-text]':text,'[data-driver-retry]':retry,'[data-driver-sync-reload]':reload}[selector]);
  const camera=new Element(),picker=new Element(),fileStatus=new Element(),fileList=new Element(),uploadSubmit=new Element(),kind=new Element(),uploadForm=new Element(),draftNote=new Element();
  draftNote.classList={toggle(){}};
  uploadForm.fields={kind:'signed_ttn'};uploadForm.action='/trips/10/documents/';
  uploadForm.querySelector=selector=>({'.driver-file-status':fileStatus,'[data-driver-selected-files]':fileList,'button[type="submit"]':uploadSubmit,'[name="kind"]':kind,'[data-upload-draft-status]':draftNote}[selector]);
  kind.dispatchEvent=()=>{};
  uploadForm.querySelectorAll=selector=>selector==='input[type="file"]'?[camera,picker]:[camera,picker,kind,uploadSubmit];
  const window={DriverUploadStore:draftStore,handlers:{},location:{redirect:null,assign(url){this.redirect=url;},reload(){}},addEventListener(name,handler){this.handlers[name]=handler;}};
  const document={body:{dataset:{driverUser:user}},visibilityState:'visible',createElement:()=>new Element(),querySelector(selector){return selector==='[data-driver-sync]'?banner:{value:'csrf'};},querySelectorAll(selector){return selector==='[data-driver-action]'?[form]:selector==='[data-driver-upload]'&&upload?[uploadForm]:[];}};
  class FormData {
    constructor(element){this.map=new Map(Object.entries(element?.fields||{}));}
    append(name,value){if(this.map.has(name))this.map.set(name,[...this.getAll(name),value]);else this.map.set(name,value);}
    set(name,value){this.map.set(name,value);}
    delete(name){this.map.delete(name);}
    getAll(name){const value=this.map.get(name);return value===undefined?[]:Array.isArray(value)?value:[value];}
    [Symbol.iterator](){return [...this.map].flatMap(([name,value])=>Array.isArray(value)?value.map(item=>[name,item]):[[name,value]])[Symbol.iterator]();}
  }
  const calls=[];
  let counter=0;
  const context={window,document,FormData,Event,URL,File:require('node:buffer').File,crypto:{randomUUID:()=> '5f46e54a-e05d-41d6-b762-'+String(++counter).padStart(12,'0')},navigator:{onLine:true},localStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)},AbortController,setTimeout,clearTimeout,setInterval(){},fetch:async(url,options)=>{calls.push({url,data:Object.fromEntries(options.body),files:options.body.getAll('file')});return fetchImpl(url,options);}};
  vm.runInNewContext(source,context);
  return {form,button,text,retry,window,storage,calls,context,camera,picker,draftNote,kind,fileStatus,fileList,uploadSubmit,upload:()=>uploadForm.handlers.submit({preventDefault(){}}),submit:()=>form.handlers.submit({preventDefault(){}})};
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const confirmed=()=>({ok:true,status:200,redirected:false,headers:{get:()=> 'application/json'},json:async()=>({outcome:'confirmed',redirect:'/'})});

test('rest request waits for server confirmation and survives connection loss',async()=>{
  const app=fixture({fetchImpl:async()=>{throw new Error('offline');}});
  app.form.dataset.syncUrl='/driver/rest/action/';
  app.form.fields={rest:'start',return_to:'/'};
  app.submit();await flush();
  assert.equal(app.window.location.redirect,null);
  assert.equal(app.storage.size,1);
  assert.equal(app.calls[0].data.rest,'start');
  const reopened=fixture({storage:app.storage,fetchImpl:async()=>confirmed()});await flush();
  assert.equal(reopened.calls[0].url,'/driver/rest/action/');
  assert.deepEqual(reopened.calls[0].data,app.calls[0].data);
  assert.equal(reopened.window.location.redirect,'/');
  assert.equal(app.storage.size,0);
});

test('rest does not discard selected photos',()=>{
  const app=fixture({fetchImpl:async()=>confirmed()});
  app.form.setAttribute('data-driver-rest','');
  app.submit();
  assert.equal(app.calls.length,0);
  assert.match(app.text.textContent,/Спочатку надішліть обрані фото/);
});

test('network loss leaves old screen, blocks next stage, and persists exact action for reload',async()=>{
  const app=fixture({fetchImpl:async()=>{throw new Error('offline');}});
  app.submit();await flush();
  assert.equal(app.window.location.redirect,null);
  assert.equal(app.button.disabled,true);
  assert.match(app.text.textContent,/ще не підтверджений/);
  assert.equal(app.storage.size,1);
  const original=app.calls[0].data;
  const reopened=fixture({storage:app.storage,fetchImpl:async()=>confirmed()});await flush();
  assert.deepEqual(reopened.calls[0].data,original);
  assert.equal(reopened.window.location.redirect,'/');
  assert.equal(app.storage.size,0);
});
test('online signal alone is never treated as server confirmation',async()=>{
  let resolveResponse;
  const app=fixture({fetchImpl:()=>new Promise(resolve=>{resolveResponse=resolve;})});
  app.submit();await flush();app.window.handlers.online();
  assert.equal(app.calls.length,1);
  assert.equal(app.window.location.redirect,null);
  assert.equal(app.button.disabled,true);
  resolveResponse(confirmed());await flush();
  assert.equal(app.window.location.redirect,'/');
});
test('another signed-in driver cannot replay someone else’s saved action',async()=>{
  const app=fixture({fetchImpl:async()=>{throw new Error('offline');}});app.submit();await flush();
  const other=fixture({storage:app.storage,user:'2',fetchImpl:async()=>confirmed()});await flush();
  assert.equal(other.calls.length,0);
  assert.equal(other.button.disabled,false);
});
test('expired session keeps pending intent and requires page refresh before retry',async()=>{
  const app=fixture({fetchImpl:async()=>({status:403,redirected:false})});app.submit();await flush();
  assert.equal(app.window.location.redirect,null);
  assert.equal(app.storage.size,1);
  assert.equal(app.button.disabled,true);
  assert.match(app.text.textContent,/підтвердити доступ/);
  app.window.handlers.online();await flush();assert.equal(app.calls.length,1);
});
test('camera and multi-selection accumulate, and a failed upload retries the same complete batch',async()=>{
  let attempts=0;
  const app=fixture({upload:true,fetchImpl:async()=>{if(!attempts++)throw new Error('lost response');return confirmed();}});
  app.camera.files=[{name:'page1.png',size:100}];app.camera.handlers.change();
  app.picker.files=[{name:'page2.png',size:100},{name:'page3.png',size:100}];app.picker.handlers.change();
  assert.equal(app.fileList.children.length,3);
  app.upload();await flush();
  assert.equal(app.calls[0].files.length,3);
  assert.equal(app.window.location.redirect,null);
  assert.match(app.fileStatus.textContent,/ще не підтвердив/);
  app.upload();await flush();
  assert.deepEqual(app.calls[1].files,app.calls[0].files);
  assert.equal(app.calls[1].data.request_id,app.calls[0].data.request_id);
  assert.equal(app.window.location.redirect,'/');
});
test('offline document selection is kept, and count limits never silently drop files',async()=>{
  const app=fixture({upload:true,fetchImpl:async()=>confirmed()});
  app.picker.files=Array.from({length:11},(_,i)=>({name:'page'+i,size:100}));app.picker.handlers.change();
  assert.match(app.fileStatus.textContent,/до 10 файлів/);assert.equal(app.calls.length,0);
  app.camera.files=[{name:'page1.png',size:100}];app.camera.handlers.change();
  app.context.navigator.onLine=false;app.upload();await flush();
  assert.equal(app.fileList.children.length,1);
  assert.equal(app.calls.length,0);assert.match(app.fileStatus.textContent,/ще не надіслано/);
  app.context.navigator.onLine=true;app.upload();await flush();assert.equal(app.calls.length,1);
});

function draftStore(){
  const rows=new Map();return {rows,get:async key=>structuredClone(rows.get(key)||null),
    put:async(row,revision)=>{if((rows.get(row.key)?.revision||null)!==revision)throw new Error('storage-conflict');rows.set(row.key,structuredClone(row));},
    remove:async(key,revision)=>{if((rows.get(key)?.revision||null)!==revision)throw new Error('storage-conflict');rows.delete(key);}};
}
const pdf=()=>new(require('node:buffer').File)(['%PDF persisted bytes'],'weighing.pdf',{type:'application/pdf',lastModified:123456});
test('photo blob survives reload and is deleted only after an acknowledged receipt',async()=>{
  const store=draftStore();const app=fixture({upload:true,draftStore:store,fetchImpl:async()=>{throw new Error('lost response');}});await flush();
  app.camera.files=[pdf()];app.camera.handlers.change();await flush();
  assert.equal(store.rows.size,1);const saved=[...store.rows.values()][0];assert.equal(await saved.files[0].blob.text(),'%PDF persisted bytes');
  await app.upload();assert.equal([...store.rows.values()][0].frozen,true);assert.equal(store.rows.size,1);
  const reopened=fixture({upload:true,draftStore:store,fetchImpl:async()=>confirmed()});await flush();
  assert.equal(reopened.fileList.children.length,1);assert.match(reopened.draftNote.textContent,/відновлено/);
  await reopened.upload();assert.equal(store.rows.size,0);assert.equal(reopened.calls[0].data.request_id,app.calls[0].data.request_id);
  assert.equal(await reopened.calls[0].files[0].text(),'%PDF persisted bytes');assert.equal(reopened.calls[0].files[0].name,'weighing.pdf');
});
test('another driver never restores a previous driver’s photo drafts',async()=>{
  const store=draftStore();const app=fixture({upload:true,draftStore:store,fetchImpl:async()=>confirmed()});await flush();app.camera.files=[pdf()];app.camera.handlers.change();await flush();
  const other=fixture({user:'2',upload:true,draftStore:store,fetchImpl:async()=>confirmed()});await flush();
  assert.equal(other.fileList.children?.length||0,0);assert.equal(store.rows.size,1);
});
test('storage quota failure is explicit and retains files in memory for upload',async()=>{
  const store=draftStore();store.put=async()=>{throw new Error('quota');};
  const app=fixture({upload:true,draftStore:store,fetchImpl:async()=>confirmed()});await flush();app.camera.files=[pdf()];app.camera.handlers.change();await flush();
  assert.match(app.draftNote.textContent,/Не вдалося зберегти фото на телефоні/);assert.equal(app.fileList.children.length,1);
  await app.upload();assert.equal(app.calls[0].files.length,1);assert.equal(app.window.location.redirect,'/');
});
test('a draft changed in another tab cannot be overwritten or sent as an obsolete batch',async()=>{
  const store=draftStore();const app=fixture({upload:true,draftStore:store,fetchImpl:async()=>confirmed()});await flush();app.camera.files=[pdf()];app.camera.handlers.change();await flush();
  const [key,row]=[...store.rows.entries()][0];row.revision='other-tab';store.rows.set(key,row);
  app.picker.files=[pdf()];app.picker.handlers.change();await flush();await app.upload();
  assert.equal(app.calls.length,0);assert.match(app.draftNote.textContent,/іншій вкладці/);assert.equal(store.rows.get(key).files.length,1);
});
test('an uncertain upload freezes file edits so retry cannot create duplicates',async()=>{
  const app=fixture({upload:true,draftStore:draftStore(),fetchImpl:async()=>{throw new Error('lost');}});await flush();app.camera.files=[pdf()];app.camera.handlers.change();await flush();await app.upload();
  assert.equal(app.camera.disabled,true);app.picker.files=[pdf()];app.picker.handlers.change();
  assert.equal(app.fileList.children.length,1);await app.upload();assert.equal(app.calls[1].data.request_id,app.calls[0].data.request_id);
});
