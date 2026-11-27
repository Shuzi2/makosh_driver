(() => {
  const trigger=document.querySelector('[data-driver-ttn-open]');
  const dialog=document.querySelector('[data-driver-ttn-dialog]');
  if(!trigger||!dialog||!dialog.showModal)return;
  const content=dialog.querySelector('[data-driver-ttn-content]');
  let controller=null;
  async function load(url){
    controller?.abort();controller=new AbortController();
    content.textContent='Завантажуємо ТТН…';
    try{
      const target=new URL(url,location.origin);target.searchParams.set('view','dialog');
      const response=await fetch(target,{credentials:'same-origin',cache:'no-store',signal:controller.signal});
      if(!response.ok||response.redirected)throw new Error('load');
      const html=await response.text();if(!dialog.open)return;
      content.innerHTML=html;content.scrollTop=0;
    }catch(error){if(error.name==='AbortError')return;content.replaceChildren();const message=document.createElement('p');message.textContent='Не вдалося завантажити ТТН. Перевірте зв’язок.';const link=document.createElement('a');link.href=trigger.href;link.textContent='Відкрити список окремо →';content.append(message,link);}
  }
  trigger.addEventListener('click',event=>{event.preventDefault();dialog.showModal();load(trigger.href);});
  dialog.querySelector('[data-driver-ttn-close]').addEventListener('click',()=>dialog.close());
  dialog.addEventListener('click',event=>{
    if(event.target===dialog){dialog.close();return;}
    const link=event.target.closest('.ttn-pagination a');if(link){event.preventDefault();load(link.href);}
  });
  dialog.addEventListener('close',()=>{controller?.abort();trigger.focus();});
})();
