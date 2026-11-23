(() => {
  const box=document.querySelector('[data-driver-notifications]');
  if(!box)return;
  const badge=box.querySelector('[data-driver-notice-count]'), list=box.querySelector('[data-driver-notice-items]');
  const tripLink=document.querySelector('[data-driver-trip-link]');
  const tripBadge=document.querySelector('[data-driver-trip-count]');
  const serviceLink=document.querySelector('[data-driver-service-link]');
  const serviceBadge=document.querySelector('[data-driver-service-count]');
  const storageKey='driver-trip-seen-'+(document.body?.dataset.driverUser||'');
  let seen=[];
  try{const saved=JSON.parse(localStorage.getItem(storageKey)||'[]');if(Array.isArray(saved))seen=saved}catch(_){}
  let latest=[];
  const serviceKey='driver-service-seen-'+(document.body?.dataset.driverUser||'');
  let serviceSeen=[],latestService=[];
  try{const saved=JSON.parse(localStorage.getItem(serviceKey)||'[]');if(Array.isArray(saved))serviceSeen=saved}catch(_){}
  function rememberService(){
    serviceSeen=[...new Set([...serviceSeen,...latestService])].slice(-200);
    try{localStorage.setItem(serviceKey,JSON.stringify(serviceSeen))}catch(_){}
    if(serviceBadge)serviceBadge.hidden=true;
  }
  function rememberTrips(){
    seen=[...new Set([...seen,...latest])].slice(-200);
    try{localStorage.setItem(storageKey,JSON.stringify(seen))}catch(_){}
    if(tripBadge)tripBadge.hidden=true;
  }
  let pending=false, last='';
  async function refresh(){
    if(pending||document.visibilityState!=='visible')return;
    pending=true;
    try {
      const response=await fetch(box.dataset.url,{cache:'no-store',credentials:'same-origin',headers:{Accept:'application/json'},signal:AbortSignal.timeout(10000)});
      if(!response.ok||!response.headers.get('content-type')?.includes('application/json'))throw new Error('unavailable');
      const data=await response.json();badge.hidden=!data.unread;badge.textContent=data.unread>99?'99+':String(data.unread);
      latest=data.items.filter(item=>item.type!=='service'&&!item.read).map(item=>item.id);
      latestService=data.items.filter(item=>item.type==='service'&&!item.read).map(item=>item.id);
      if(serviceLink?.getAttribute('aria-current')==='page')rememberService();
      else if(serviceBadge){const count=latestService.filter(id=>!serviceSeen.includes(id)).length;serviceBadge.hidden=!count;serviceBadge.textContent=count>99?'99+':String(count);serviceBadge.setAttribute('aria-label','Нових оновлень сервісу: '+count)}
      if(tripLink?.getAttribute('aria-current')==='page')rememberTrips();
      else if(tripBadge){const count=latest.filter(id=>!seen.includes(id)).length;tripBadge.hidden=!count;tripBadge.textContent=count>99?'99+':String(count);tripBadge.setAttribute('aria-label','Нових призначень: '+count)}
      const key=JSON.stringify(data.items);if(key===last)return;last=key;
      const fragment=document.createDocumentFragment();
      data.items.forEach(item=>{
        const link=document.createElement('a');link.href=item.url;link.className='driver-notice-item'+(item.read?'':' is-unread');
        const title=document.createElement('strong');title.textContent=item.type==='service'?'Сервіс · '+item.title:'Рейс №'+item.trip+' · '+item.vehicle;
        const info=document.createElement('span');info.textContent=(item.type==='service'?(item.message||'Оновлення сервісу'):item.type==='route'?'Маршрут змінено · підтвердьте ознайомлення':'Нове призначення')+' · '+item.time;
        link.append(title,info);
        if(!item.read && item.type!=='route')link.addEventListener('click',async event=>{
          event.preventDefault();
          try{const token=document.querySelector('input[name=csrfmiddlewaretoken]')?.value;const result=await fetch(box.dataset.readUrl,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/x-www-form-urlencoded','X-CSRFToken':token},body:new URLSearchParams({id:item.id}),signal:AbortSignal.timeout(5000)});if(result.ok){link.classList.remove('is-unread');const count=Math.max(0,data.unread-1);badge.hidden=!count;badge.textContent=count>99?'99+':String(count)}}catch(_){}
          window.location.assign(item.url);
        });
        fragment.append(link);
      });
      if(!data.items.length){const empty=document.createElement('p');empty.textContent='Нових повідомлень немає.';fragment.append(empty)}
      list.replaceChildren(fragment);
    }catch(_){
      last='';
      const error=document.createElement('p');error.className='driver-notice-error';error.setAttribute('role','status');
      error.textContent='Не вдалося оновити повідомлення. Спробуємо ще раз автоматично.';
      list.replaceChildren(error);
    }finally{pending=false}
  }
  refresh();setInterval(refresh,5000);
  tripLink?.addEventListener('click',rememberTrips);
  serviceLink?.addEventListener('click',rememberService);
  box.addEventListener('driver-menu-open',refresh);
  document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')refresh()});
})();
