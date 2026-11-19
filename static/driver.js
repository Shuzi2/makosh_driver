// Never show a new trip stage before the server has acknowledged it.
(() => {
  const user=document.body.dataset.driverUser;
  if (!user) return;
  const key='makosh-driver-pending-v1-'+user;
  const banner=document.querySelector('[data-driver-sync]');
  const text=banner.querySelector('[data-driver-sync-text]');
  const retry=banner.querySelector('[data-driver-retry]');
  const reload=banner.querySelector('[data-driver-sync-reload]');
  const forms=[...document.querySelectorAll('[data-driver-action]')];
  let pending=null,sending=false,storageAvailable=true,blocked=false;
  const newId=()=>crypto.randomUUID ? crypto.randomUUID() : '10000000-1000-4000-8000-100000000000'.replace(/[018]/g,c=>(c^crypto.getRandomValues(new Uint8Array(1))[0]&15>>c/4).toString(16));
  function readPending(){try{return JSON.parse(localStorage.getItem(key)||'null');}catch(error){storageAvailable=false;return null;}}
  function storePending(value){pending=value;try{if(value)localStorage.setItem(key,JSON.stringify(value));else localStorage.removeItem(key);}catch(error){storageAvailable=false;}window.driverHasPendingAction=Boolean(value);}
  function setControls(){forms.forEach(form=>form.querySelectorAll('button[type="submit"]').forEach(button=>{if(!button.hasAttribute('data-original-disabled'))button.dataset.originalDisabled=button.disabled?'1':'0';button.disabled=Boolean(pending)||blocked||button.dataset.originalDisabled==='1';}));}
  function show(message,terminal=false){banner.hidden=false;text.textContent=message;retry.hidden=terminal;reload.hidden=!terminal;setControls();}
  function csrf(){return document.querySelector('input[name="csrfmiddlewaretoken"]')?.value||'';}
  async function post(url,data,timeoutMs=20000){
    const controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),timeoutMs);
    try{return await fetch(url,{method:'POST',body:data,credentials:'same-origin',cache:'no-store',redirect:'follow',headers:{Accept:'application/json','X-CSRFToken':csrf()},signal:controller.signal});}
    finally{clearTimeout(timeout);}
  }
  async function sendPending(){
    if(!pending||sending||blocked)return;
    // Only replay the locally recorded action for this signed-in driver.
    if(!/^\/driver\/(?:trips\/\d+|rest)\/action\/$/.test(pending.url)||!pending.data){storePending(null);return;}
    sending=true;retry.disabled=true;
    show('Надсилаємо зміну… Статус ще не підтверджений сервером.');
    try{
      const data=new FormData();Object.entries(pending.data).forEach(([name,value])=>data.append(name,value));
      const response=await post(pending.url,data);
      if(response.status===404){storePending(null);blocked=true;show('Рейс уже недоступний. Оновіть дані або зверніться до логіста.',true);return;}
      if(response.redirected||[401,403].includes(response.status)){blocked=true;show('Не вдалося підтвердити доступ. Оновіть сторінку та, за потреби, увійдіть знову. Зміна очікує підтвердження.',true);return;}
      if(!response.headers.get('content-type')?.includes('application/json'))throw new Error('session');
      const result=await response.json();
      if(response.ok&&['confirmed','reminder'].includes(result.outcome)){
        if(result.outcome==='confirmed'&&pending.data.loaded_distance_km!==undefined&&pending.data.fuel_used_l!==undefined){try{localStorage.removeItem('makosh-finish-draft-'+user+'-'+pending.url);}catch(error){}}
        storePending(null);blocked=true;
        if(result.outcome==='confirmed')banner.hidden=true;
        else show('Перевірте дані завершення та ТТН. Рейс ще не завершено.',true);
        window.location.assign(result.redirect);
      }else if(result.outcome==='rejected'||[400,403,404,409].includes(response.status)){
        storePending(null);blocked=true;show(result.message||'Дію не збережено. Оновіть дані рейсу.',true);
      }else throw new Error('unavailable');
    }catch(error){
      show('Статус ще не підтверджений сервером. Логіст міг ще не отримати зміну. Повторимо надсилання, коли з’явиться зв’язок.'+(!storageAvailable?' Не закривайте цю сторінку.':''));
    }finally{sending=false;retry.disabled=false;setControls();}
  }
  forms.forEach(form=>form.addEventListener('submit',event=>{
    event.preventDefault();
    if(pending||blocked){sendPending();return;}
    if(form.hasAttribute('data-driver-rest')&&document.querySelector('[data-driver-dirty]')){
      show('Спочатку надішліть обрані фото, потім змініть стан відпочинку. Фото ще не збережені.');return;
    }
    document.querySelectorAll('.driver-main .message').forEach(message=>message.remove());
    const data=Object.fromEntries(new FormData(form));delete data.csrfmiddlewaretoken;
    data.request_id=newId();data.expected_revision=form.dataset.revision;
    storePending({url:form.dataset.syncUrl,data});setControls();sendPending();
  }));
  pending=readPending();window.driverHasPendingAction=Boolean(pending);setControls();
  if(pending){show('Є зміна, яку сервер ще не підтвердив. Повторюємо надсилання.');sendPending();}
  else if(!navigator.onLine)show('Немає інтернету. Показано останній отриманий статус; зміни потребують підтвердження сервера.');
  retry.addEventListener('click',()=>{if(pending)sendPending();else window.location.reload();});
  window.addEventListener('online',()=>{if(pending)sendPending();else if(!blocked)banner.hidden=true;});
  window.addEventListener('offline',()=>show(pending?'Статус ще не підтверджений сервером. Очікуємо зв’язок.':'Немає інтернету. Показано останній отриманий статус; зміни потребують підтвердження сервера.'));
  window.addEventListener('storage',event=>{if(event.key===key){pending=readPending();window.driverHasPendingAction=Boolean(pending);setControls();if(pending)sendPending();}});
  setInterval(()=>{if(document.visibilityState==='visible'&&pending)sendPending();},15000);

  window.driverUploadPost=post;
})();
