(() => {
  const user=document.body.dataset.driverUser;if(!user||!window.driverUploadPost)return;
  const store=window.DriverUploadStore;
  const confirmation=document.querySelector('[data-upload-result]');
  if(confirmation){try{const receipt=JSON.parse(sessionStorage.getItem('mtruck-upload-confirmed')||'null');const shownTrip=new URLSearchParams(window.location.search).get('trip');if(receipt&&Date.now()-receipt.at<120000&&(!shownTrip||shownTrip===receipt.trip)){confirmation.textContent='✓ Документи надіслано. Сервер підтвердив збереження.';confirmation.hidden=false;}sessionStorage.removeItem('mtruck-upload-confirmed');}catch(error){/* Browser storage can be unavailable. */}}
  const newId=()=>crypto.randomUUID ? crypto.randomUUID() : '10000000-1000-4000-8000-100000000000'.replace(/[018]/g,c=>(c^crypto.getRandomValues(new Uint8Array(1))[0]&15>>c/4).toString(16));
  document.querySelectorAll('[data-driver-upload]').forEach(form=>{
    const inputs=[...form.querySelectorAll('input[type="file"]')],status=form.querySelector('.driver-file-status'),list=form.querySelector('[data-driver-selected-files]'),submit=form.querySelector('button[type="submit"]'),note=form.querySelector('[data-upload-draft-status]');
    const controls=[...form.querySelectorAll('button,input,select')],disabled=controls.map(c=>c.disabled);
    const trip=form.dataset.trip||form.action.match(/trips\/(\d+)/)?.[1]||'',stop=form.dataset.stop||'';
    const key=[user,trip,stop].join(':');
    let selected=[],batchId=newId(),uploading=false,frozen=false,revision=null,queue=Promise.resolve(),conflict=false,metadata=null,previews=[],saving=0,storageFailed=!store;
    window.addEventListener('beforeunload',event=>{if(selected.length&&(saving||storageFailed)){event.preventDefault();event.returnValue='';}});
    const readMeta=()=>{const data=Object.fromEntries(new FormData(form));return {kind:data.kind??'ttn',stop:data.stop||'',ttn_number:data.ttn_number||''};};
    const originallyDisabled=(control,i)=>control.name==='ttn_number'?!['ttn','signed_ttn'].includes(form.querySelector('[name="kind"]')?.value):disabled[i];
    function announce(message,error=false){if(note){note.hidden=false;note.textContent=message;note.classList.toggle('is-error',error);}}
    function changed(){window.dispatchEvent?.(new Event('driver:drafts'));}
    function lock(){
      controls.forEach((control,i)=>{control.disabled=originallyDisabled(control,i)||uploading||conflict||(frozen&&control!==submit);});
      list.querySelectorAll?.('button').forEach(button=>button.disabled=uploading||conflict);
    }
    function render(){
      previews.forEach(url=>URL.revokeObjectURL(url));previews=[];list.replaceChildren();
      selected.forEach((file,index)=>{
        const li=document.createElement('li'),label=document.createElement('span');label.textContent=(index+1)+'. '+file.name;
        if(file.type?.startsWith('image/')&&URL.createObjectURL){const img=document.createElement('img'),url=URL.createObjectURL(file);previews.push(url);img.src=url;img.alt='Обране фото';li.append(img);}
        const remove=document.createElement('button');remove.type='button';remove.textContent='Прибрати';remove.setAttribute('aria-label','Прибрати '+file.name);
        remove.addEventListener('click',()=>{
          if(uploading||conflict)return;
          if(frozen&&!window.confirm('Сервер не підтвердив відповідь, але фото могло вже зберегтися. Перевірте документи рейсу перед повторним надсиланням, щоб уникнути дублювання. Прибрати це фото з телефону?'))return;
          if(frozen){frozen=false;metadata=null;}
          selected.splice(index,1);batchId=newId();render();save();
        });
        li.append(label,remove);list.append(li);
      });
      status.textContent=selected.length?'Обрано файлів: '+selected.length+'. Фото готові до надсилання.':'Зробіть фото або оберіть кілька файлів.';
      if(selected.length)form.setAttribute('data-driver-dirty','true');else form.removeAttribute('data-driver-dirty');lock();
    }
    function save(){
      if(!store)return Promise.resolve();
      saving++;
      const meta=frozen?metadata:readMeta();
      const row={key,user:String(user),trip,stop,place:form.dataset.place||'',kind:meta.kind,number:meta.ttn_number,metadata:meta,batchId,frozen,updatedAt:Date.now(),revision:newId(),files:selected.map(file=>({blob:file,name:file.name,type:file.type,lastModified:file.lastModified}))};
      queue=queue.then(async()=>{
        if(conflict){saving--;return;}
        try{
          if(row.files.length){await store.put(row,revision);revision=row.revision;announce(frozen?'Фото збережено на телефоні. Повторне надсилання перевірить ту саму спробу.':'Фото збережено на цьому телефоні. Натисніть «Надіслати», коли буде зв’язок.');}
          else if(revision){await store.remove(key,revision);revision=null;if(note)note.hidden=true;}
          storageFailed=false;changed();
        }catch(error){
          conflict=error.message==='storage-conflict';storageFailed=true;
          announce(conflict?'Ці фото змінені в іншій вкладці. Відкрийте документи заново, щоб не втратити зміни.':'Не вдалося зберегти фото на телефоні. Не закривайте цю сторінку до надсилання.',true);lock();
        }finally{saving--;}
      });return queue;
    }
    let ready=Promise.resolve();
    window.addEventListener('driver:draft-discard',event=>{
      if(event.detail.key!==key||uploading)return;
      selected=[];revision=null;frozen=false;conflict=false;metadata=null;batchId=newId();if(note)note.hidden=true;render();
    });
    if(store){
      uploading=true;lock();
      ready=(async()=>{
        try{
          const row=await store.get(key);
          if(row&&row.files.length){
            revision=row.revision;batchId=row.batchId;frozen=row.frozen;metadata=row.metadata;
            selected=row.files.map(f=>new File([f.blob],f.name,{type:f.type,lastModified:f.lastModified}));
            Object.entries(metadata).forEach(([name,value])=>{const field=form.querySelector('[name="'+name+'"]');if(field){field.value=value;field.dispatchEvent(new Event('change',{bubbles:true}));}});
            announce(frozen?'Фото відновлено. Сервер ще не підтвердив попередню спробу — повторіть надсилання.':'Ненадіслані фото відновлено з цього телефону.');
          }
        }catch(error){storageFailed=true;announce('Збереження на телефоні недоступне. Не закривайте сторінку з обраними фото.',true);}
        finally{uploading=false;render();}
      })();
    }
    inputs.forEach(input=>input.addEventListener('change',()=>{
      if(uploading||frozen||conflict)return;
      const incoming=[...input.files];input.value='';
      if(selected.length+incoming.length>10){status.textContent='Можна надіслати до 10 файлів за раз.';return;}
      if(incoming.some(file=>file.size>20*1024*1024)||[...selected,...incoming].reduce((sum,file)=>sum+file.size,0)>40*1024*1024){status.textContent='Кожен файл — до 20 МБ, разом — до 40 МБ.';return;}
      selected.push(...incoming);batchId=newId();render();save();
    }));
    ['kind','ttn_number'].forEach(name=>form.querySelector('[name="'+name+'"]')?.addEventListener('change',()=>{if(uploading||frozen||conflict)return;batchId=newId();save();}));
    form.addEventListener('submit',async event=>{
      event.preventDefault();if(uploading||conflict)return;uploading=true;lock();
      await ready;await queue;
      if(conflict){uploading=false;lock();return;}
      if(!selected.length){status.textContent='Спочатку додайте фото або PDF.';uploading=false;lock();return;}
      if(!navigator.onLine){status.textContent='Немає інтернету. Фото ще не надіслано. Після відновлення зв’язку натисніть «Надіслати».';uploading=false;lock();return;}
      // Capture before disabling controls; immutable payload allows an exact retry after a lost response.
      if(!frozen){controls.forEach((c,i)=>c.disabled=originallyDisabled(c,i));metadata=readMeta();frozen=true;}
      await save();if(conflict){uploading=false;lock();return;}
      const data=new FormData(form);data.delete('file');Object.entries(metadata).forEach(([name,value])=>data.set(name,value));selected.forEach(file=>data.append('file',file));data.set('request_id',batchId);
      lock();form.setAttribute('aria-busy','true');submit.textContent='Надсилаємо…';status.textContent='Фото надсилаються. Очікуємо підтвердження…';
      try{
        const response=await window.driverUploadPost(form.action,data,120000);
        if(response.status===413){
          frozen=false;metadata=null;batchId=newId();controls.forEach((c,i)=>c.disabled=originallyDisabled(c,i));await save();
          status.textContent='Обсяг фото завеликий для сервера. Приберіть частину файлів і надішліть їх окремими групами.';
          uploading=false;form.removeAttribute('aria-busy');lock();submit.textContent=submit.dataset.uploadLabel||'Надіслати документи ↑';return;
        }
        if(response.redirected||!response.headers.get('content-type')?.includes('application/json'))throw new Error('session');
        const result=await response.json();
        if(response.ok&&result.outcome==='confirmed'){
          try{sessionStorage.setItem('mtruck-upload-confirmed',JSON.stringify({trip:String(trip),at:Date.now()}));}catch(error){/* The confirmed documents remain in the list. */}
          if(store&&revision){try{await store.remove(key,revision);revision=null;changed();}catch(error){/* Exact receipt remains safe to retry if browser storage is unavailable. */}}
          selected=[];form.removeAttribute('data-driver-dirty');status.textContent=result.message;window.location.assign(result.redirect);return;
        }
        if(response.status===400&&result.outcome==='rejected'){frozen=false;batchId=newId();controls.forEach((c,i)=>c.disabled=originallyDisabled(c,i));await save();}
        status.textContent=result.message||'Документи ще не підтверджені. Перевірте доступ до рейсу та повторіть надсилання.';
      }catch(error){status.textContent=error.message==='session'?'Доступ або відповідь сервера не підтверджено. Перевірте вхід і відкрийте документи знову; фото збережені на телефоні, якщо пам’ять доступна.':'Сервер ще не підтвердив збереження фото. Повторіть надсилання. Повторна спроба не дублює файли.';}
      uploading=false;form.removeAttribute('aria-busy');lock();submit.textContent=submit.dataset.uploadLabel||'Надіслати документи ↑';
    });
  });
})();
