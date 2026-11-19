(() => {
  const user=document.body.dataset.driverUser,store=window.DriverUploadStore,banner=document.querySelector('[data-photo-drafts]');
  if(!user||!store||!banner)return;
  const dialog=document.querySelector('[data-photo-draft-viewer]');let selected=null,urls=[],opener=null;
  const local=name=>dialog.querySelector('[data-local-'+name+']');
  async function open(draft,button){
    if(!dialog?.showModal)return;
    try{
      const row=await store.get(draft.key);if(!row||row.user!==String(user)){update();return;}
      selected=row;opener=button;local('title').textContent='Рейс №'+row.trip+' · '+row.files.length+' фото';local('message').textContent='';local('files').replaceChildren();
      row.files.forEach(file=>{const url=URL.createObjectURL(file.blob);urls.push(url);const link=document.createElement('a');link.href=url;link.download=file.name;
        if(file.type?.startsWith('image/')){const image=document.createElement('img');image.src=url;image.alt='Фото з чернетки';link.append(image);}
        const name=document.createElement('span');name.textContent=file.name+' · Зберегти копію ↓';link.append(name);local('files').append(link);
      });dialog.showModal();
    }catch(error){button.textContent='Не вдалося відкрити. Спробуйте ще раз.';}
  }
  async function update(){
    try{
      const drafts=(await store.list(user)).filter(row=>row.files.length).sort((a,b)=>b.updatedAt-a.updatedAt);
      banner.hidden=!drafts.length;banner.querySelector('[data-photo-drafts-title]').textContent='Ненадіслані фото на цьому телефоні: '+drafts.reduce((n,d)=>n+d.files.length,0);
      const links=banner.querySelector('[data-photo-drafts-links]');links.replaceChildren();
      drafts.forEach(draft=>{
        const row=document.createElement('div'),link=document.createElement('a');link.href='/driver/documents/?'+new URLSearchParams({trip:draft.trip,stop:draft.stop,kind:draft.kind,ttn_number:draft.number});link.textContent='Рейс №'+draft.trip+' · '+(draft.place||'Документи')+' · '+draft.files.length+' фото — надіслати';
        const button=document.createElement('button');button.type='button';button.className='link';button.textContent='Фото на телефоні';button.addEventListener('click',()=>open(draft,button));row.append(link,button);links.append(row);
      });
    }catch(error){banner.hidden=true;}
  }
  update();window.addEventListener('driver:drafts',update);window.addEventListener('focus',update);
  if(dialog){
    local('close').addEventListener('click',()=>dialog.close());
    dialog.addEventListener('close',()=>{urls.forEach(url=>URL.revokeObjectURL(url));urls=[];selected=null;local('files').replaceChildren();opener?.focus();});
    local('discard').addEventListener('click',async()=>{
      if(!selected)return;
      if(document.querySelector('form[aria-busy="true"]')){local('message').textContent='Спочатку дочекайтеся результату надсилання.';return;}
      if(!window.confirm('Прибрати чернетку з цього телефону? Ненадіслані фото буде видалено. Спочатку збережіть потрібні копії.'+(selected.frozen?' Сервер ще не підтвердив надсилання — перевірте документи рейсу.':'')))return;
      try{await store.remove(selected.key,selected.revision);window.dispatchEvent(new CustomEvent('driver:draft-discard',{detail:{key:selected.key}}));window.dispatchEvent(new Event('driver:drafts'));dialog.close();}
      catch(error){local('message').textContent='Чернетка вже змінилася або пам’ять недоступна. Закрийте вікно й відкрийте знову.';}
    });
  }
})();
