(() => {
  const dialog=document.querySelector('[data-quick-dialog]');
  if(!dialog||typeof dialog.showModal!=='function')return;
  let opener=null;
  document.querySelectorAll('[data-quick-edit]').forEach(link=>link.addEventListener('click',event=>{
    const panel=[...dialog.querySelectorAll('[data-edit-panel]')].find(item=>item.dataset.editPanel===link.dataset.quickEdit);
    if(!panel)return;
    event.preventDefault();opener=link;
    dialog.querySelectorAll('[data-edit-panel]').forEach(item=>item.hidden=item!==panel);
    panel.querySelector('form').reset();
    dialog.querySelector('#quick-edit-title').textContent=panel.dataset.editTitle;
    dialog.showModal();panel.querySelector('input:not([type="hidden"]),textarea,select')?.focus();
  }));
  dialog.querySelectorAll('[data-close-edit]').forEach(button=>button.addEventListener('click',()=>dialog.close()));
  dialog.addEventListener('click',event=>{
    const rect=dialog.getBoundingClientRect();
    if(event.target===dialog&&(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom))dialog.close();
  });
  dialog.addEventListener('close',()=>opener?.focus());
  dialog.querySelectorAll('form').forEach(form=>form.addEventListener('submit',()=>{
    if(!form.checkValidity())return;
    const button=form.querySelector('button[type="submit"]');button.disabled=true;button.textContent='Зберігаємо…';
  }));
})();

// Refresh confirmed trip changes without discarding open editors or upload drafts.
(() => {
  const page=document.querySelector('[data-trip-live-url]');
  if(!page)return;
  const note=page.querySelector('[data-trip-live-note]');
  let dirty=false,checking=false;
  page.addEventListener('input',event=>{if(event.target.closest('form'))dirty=true;});
  page.addEventListener('change',event=>{if(event.target.closest('form'))dirty=true;});
  async function check(){
    if(checking||document.visibilityState!=='visible')return;
    checking=true;
    try{
      const response=await fetch(page.dataset.tripLiveUrl,{cache:'no-store',credentials:'same-origin',headers:{Accept:'application/json'},signal:AbortSignal.timeout(10000)});
      if(response.redirected||!response.ok||!response.headers.get('content-type')?.includes('application/json'))throw new Error('unavailable');
      const data=await response.json();
      if(typeof data.version!=='string')throw new Error('invalid');
      const driverMessages=page.querySelector('[data-driver-messages]');
      if(driverMessages && typeof data.driver_messages_html==='string' && driverMessages.innerHTML!==data.driver_messages_html){
        driverMessages.innerHTML=data.driver_messages_html;
      }
      if(data.version!==page.dataset.tripVersion){
        if(dirty||document.querySelector('dialog[open]')){
          note.hidden=false;note.textContent='Рейс оновився. Завершіть редагування, потім оновіть сторінку, щоб побачити нові дані.';
          return;
        }
        window.location.reload();return;
      }
      note.hidden=true;
    }catch(error){note.hidden=false;note.textContent='Немає зв’язку із сервером. Показано останні отримані дані рейсу.';}
    finally{checking=false;}
  }
  setInterval(check,5000);
  document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')check();});
  window.addEventListener('online',check);
})();
