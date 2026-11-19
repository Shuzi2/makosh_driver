// Blob drafts stay on this browser until an acknowledged server receipt.
(() => {
  let database;
  function db(){
    if(!database)database=new Promise((resolve,reject)=>{
      if(!window.indexedDB){reject(new Error('storage-unavailable'));return;}
      const request=window.indexedDB.open('makosh-driver-photos-v1',1);
      request.onupgradeneeded=()=>request.result.createObjectStore('drafts',{keyPath:'key'});
      request.onsuccess=()=>{request.result.onversionchange=()=>{request.result.close();database=null;};resolve(request.result);};
      request.onerror=()=>{database=null;reject(request.error);};
      request.onblocked=()=>{database=null;reject(new Error('storage-blocked'));};
    });return database;
  }
  async function run(mode,work){
    const database=await db();return new Promise((resolve,reject)=>{
      const tx=database.transaction('drafts',mode);let result;
      tx.oncomplete=()=>resolve(result);tx.onerror=()=>reject(tx.error||new Error('storage-failed'));tx.onabort=()=>reject(tx.error||new Error('storage-conflict'));
      work(tx.objectStore('drafts'),value=>{result=value;},tx);
    });
  }
  window.DriverUploadStore={
    get:key=>run('readonly',(store,done)=>{const r=store.get(key);r.onsuccess=()=>done(r.result||null);}),
    list:user=>run('readonly',(store,done)=>{const r=store.getAll();r.onsuccess=()=>done(r.result.filter(row=>row.user===String(user)));}),
    put:(row,expected)=>run('readwrite',(store,done,tx)=>{const r=store.get(row.key);r.onsuccess=()=>{if((r.result?.revision||null)!==expected){tx.abort();return;}store.put(row);done(row);};}),
    remove:(key,expected)=>run('readwrite',(store,done,tx)=>{const r=store.get(key);r.onsuccess=()=>{if((r.result?.revision||null)!==expected){tx.abort();return;}store.delete(key);done(true);};}),
  };
})();
