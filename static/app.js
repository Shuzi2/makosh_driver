const search=document.querySelector('#fleet-search');
// Confirm consequential actions without losing the clicked submit button's value.
document.addEventListener('submit',event=>{
  const form=event.target, button=event.submitter;
  if(!(form instanceof HTMLFormElement)) return;
  if(form.dataset.confirmed==='yes'){delete form.dataset.confirmed;return}
  if(!button?.dataset.confirmTitle) return;
  event.preventDefault();
  const title=button.dataset.confirmTitle, detail=button.dataset.confirmDetail || '';
  const dialog=document.createElement('dialog');
  if(typeof dialog.showModal!=='function'){
    if(window.confirm(title+'\n\n'+detail)){form.dataset.confirmed='yes';form.requestSubmit(button)}
    return;
  }
  dialog.className='fleet-confirm';
  dialog.setAttribute('aria-labelledby','fleet-confirm-title');
  dialog.setAttribute('aria-describedby','fleet-confirm-detail');
  dialog.innerHTML='<div class="fleet-confirm-icon" aria-hidden="true">!</div><h2 id="fleet-confirm-title"></h2><p id="fleet-confirm-detail"></p><div class="fleet-confirm-actions"><button type="button" class="btn" data-cancel>Повернутися</button><button type="button" class="btn danger" data-confirm>Підтвердити</button></div>';
  dialog.querySelector('h2').textContent=title;
  dialog.querySelector('p').textContent=detail;
  document.body.appendChild(dialog);
  dialog.addEventListener('close',()=>{dialog.remove();if(button.isConnected) button.focus()},{once:true});
  dialog.querySelector('[data-cancel]').addEventListener('click',()=>dialog.close());
  dialog.querySelector('[data-confirm]').addEventListener('click',()=>{dialog.close();form.dataset.confirmed='yes';form.requestSubmit(button)});
  dialog.showModal();dialog.querySelector('[data-cancel]').focus();
});
document.querySelectorAll('[data-filter-form]').forEach(form=>form.addEventListener('submit',event=>{
  if(event.defaultPrevented)return;
  const button=event.submitter;
  if(button){button.textContent='Шукаємо…';form.setAttribute('aria-busy','true')}
}));
document.querySelectorAll('[data-auto-submit]').forEach(select=>select.addEventListener('change',()=>select.form?.requestSubmit()));
// Routine confirmations leave room for the page; warnings and errors stay in place.
const confirmations=[...document.querySelectorAll('main > .message.success')];
if(confirmations.length){
  const stack=document.createElement('div');
  stack.className='fleet-toast-stack';
  document.body.appendChild(stack);
  confirmations.forEach(message=>{
    const text=document.createElement('span');
    text.className='toast-text';
    while(message.firstChild) text.appendChild(message.firstChild);
    const close=document.createElement('button');
    close.type='button';close.className='toast-close';close.setAttribute('aria-label','Закрити повідомлення');close.textContent='×';
    close.addEventListener('click',()=>message.remove());
    message.append(text,close);stack.appendChild(message);
    const dismiss=()=>{if(message.matches(':hover')||message.contains(document.activeElement)){setTimeout(dismiss,4000);return} message.remove()};
    setTimeout(dismiss,12000);
  });
}
// Keep the header's location label and scroll elevation in sync with the page.
const fleetHeader=document.querySelector('.cargo-header');
if(fleetHeader){
  const section=fleetHeader.querySelector('[data-header-section]');
  const heading=document.querySelector('.app > main h1');
  const activeNav=document.querySelector('nav[aria-label="Головна навігація"] a[aria-current="page"] span:not(.nav-icon)');
  if(section) section.textContent=heading?.textContent.trim() || activeNav?.textContent.trim() || 'Автопарк';
  const updateHeader=()=>fleetHeader.classList.toggle('is-scrolled',window.scrollY>8);
  updateHeader();
  window.addEventListener('scroll',updateHeader,{passive:true});
}
// Elapsed time uses the recorded stage timestamp, never the page opening time.
function updateStageClocks(){
  document.querySelectorAll('[data-stage-since]').forEach(clock=>{
    const start=Date.parse(clock.dataset.stageSince);
    if(!Number.isFinite(start))return;
    const minutes=Math.max(0,Math.floor((Date.now()-start)/60000));
    const days=Math.floor(minutes/1440),hours=Math.floor(minutes%1440/60),mins=minutes%60;
    clock.textContent=days?`${days} дн ${hours} год`:hours?`${hours} год ${mins} хв`:mins?`${mins} хв`:'щойно';
  });
}
updateStageClocks();setInterval(updateStageClocks,60000);
let fleetFilter='';
const fleetMetrics=document.querySelector('#fleet-metrics');
function syncFleetFilters(){
  fleetMetrics?.querySelectorAll('[data-fleet-filter]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.fleetFilter===fleetFilter)));
  if(search){
    const query=search.value.trim().toLocaleLowerCase('uk');
    document.querySelectorAll('.searchable').forEach(card=>{
      card.hidden=!card.textContent.toLocaleLowerCase('uk').includes(query) || Boolean(fleetFilter && card.dataset.fleetState!==fleetFilter);
    });
    document.querySelectorAll('.fleet-group').forEach(group=>{
      const cards=[...group.querySelectorAll('.searchable')];
      const noMatches=Boolean(query || fleetFilter) && cards.length>0 && cards.every(card=>card.hidden);
      const empty=group.querySelector('.fleet-search-empty');
      empty.textContent=fleetFilter?'Авто за обраним фільтром не знайдено.':'За цим запитом авто не знайдено.';
      empty.hidden=!noMatches;
    });
  }
}
if(search){
  search.addEventListener('input',syncFleetFilters);
  fleetMetrics?.addEventListener('click',event=>{
    const button=event.target.closest('[data-fleet-filter]');
    if(!button || !fleetMetrics.contains(button))return;
    fleetFilter=fleetFilter===button.dataset.fleetFilter?'':button.dataset.fleetFilter;
    syncFleetFilters();
  });
}
// Do not cache private pages or files in a service worker.

// Refresh dispatcher cards in place; never interrupt menus, searches or forms.
const liveFleet=document.querySelector('[data-fleet-live-url]');
if (liveFleet) {
  const liveNote=document.querySelector('[data-fleet-live-status]');
  let updating=false;
  async function updateFleet() {
    if (updating || document.visibilityState!=='visible') return;
    updating=true;
    const indicator=setTimeout(()=>{liveNote.dataset.state='loading';liveNote.textContent='Оновлюємо статуси…'},450);
    try {
      const response=await fetch(liveFleet.dataset.fleetLiveUrl,{cache:'no-store',headers:{Accept:'application/json'},signal:AbortSignal.timeout(10000)});
      if (!response.ok || !response.headers.get('content-type')?.includes('application/json')) throw new Error('Unavailable');
      const data=await response.json();
      // An open action menu must keep its focus and current target.
      if (!liveFleet.querySelector('.vehicle-menu[open]') && !liveFleet.contains(document.activeElement)) {
        const fragment=new DOMParser().parseFromString(data.cards,'text/html');
        const incoming=[...fragment.querySelectorAll('[data-vehicle-id]')];
        const existing=new Map([...liveFleet.querySelectorAll('[data-vehicle-id]')].map(card=>[card.dataset.vehicleId,card]));
        // Preserve each trip's open state while fresh status and document data arrive.
        incoming.forEach(card=>{
          const oldDetails=existing.get(card.dataset.vehicleId)?.querySelector('.vehicle-trip-details');
          const newDetails=card.querySelector('.vehicle-trip-details');
          if(oldDetails && newDetails && oldDetails.dataset.tripId===newDetails.dataset.tripId) newDetails.open=oldDetails.open;
        });
        const sameStructure=incoming.length===existing.size && incoming.every(card=>existing.has(card.dataset.vehicleId)) &&
          [...fragment.querySelectorAll('.fleet-group')].map(group=>[...group.querySelectorAll('[data-vehicle-id]')].map(card=>card.dataset.vehicleId).join(',')).join('|')===[...liveFleet.querySelectorAll('.fleet-group')].map(group=>[...group.querySelectorAll('[data-vehicle-id]')].map(card=>card.dataset.vehicleId).join(',')).join('|');
        const expandedCard=liveFleet.querySelector('.fleet-full-route[open]');
        if (!sameStructure) {if(!expandedCard) liveFleet.innerHTML=fragment.body.innerHTML;}
        else incoming.forEach(card=>{
          const old=existing.get(card.dataset.vehicleId);
          if(!old||old.querySelector('.fleet-full-route[open]'))return;
          const copy=old.cloneNode(true);copy.removeAttribute('hidden');
          if (copy.outerHTML!==card.outerHTML) old.replaceWith(card);
        });
        const metrics=document.querySelector('#fleet-metrics');
        if (metrics) {
          const incomingMetrics=new DOMParser().parseFromString(data.metrics,'text/html');
          const signature=root=>[...root.querySelectorAll('[data-fleet-filter]')].map(button=>button.dataset.fleetFilter+':'+button.querySelector('strong').textContent).join('|');
          if(signature(metrics)!==signature(incomingMetrics)) metrics.innerHTML=data.metrics;
        }
        const vehicleCount=document.querySelector('[data-fleet-vehicle-count]');
        if (vehicleCount && Number.isInteger(data.vehicle_count)) vehicleCount.textContent=String(data.vehicle_count);
        syncFleetFilters();
      }
      liveNote.textContent='';
      liveNote.dataset.state='ready';
    } catch (error) {
      liveNote.textContent='Немає оновлень. Повторюємо підключення…';
      liveNote.dataset.state='error';
    } finally { clearTimeout(indicator);updating=false; }
  }
  setInterval(updateFleet,5000);
  document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible') updateFleet();});
}

// A vehicle suggests its permanent driver; the dispatcher can choose a substitute.
const vehicleSelect = document.querySelector('#id_vehicle');
const driverSelect = document.querySelector('#id_driver');
if (vehicleSelect && driverSelect) {
  vehicleSelect.addEventListener('change', () => {
    const driverId = vehicleSelect.selectedOptions[0]?.dataset.driver || '';
    driverSelect.value = [...driverSelect.options].some(option => option.value === driverId) ? driverId : '';
  });
}

// Native details controls remain usable with keyboard or without JavaScript.
document.addEventListener('click', event => {
  document.querySelectorAll('.vehicle-menu[open]').forEach(menu => {
    if (!menu.contains(event.target)) menu.open = false;
  });
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape') {
    document.querySelectorAll('.vehicle-menu[open]').forEach(menu => {
      const focused = menu.contains(document.activeElement);
      menu.open = false;
      if (focused) menu.querySelector('summary').focus();
    });
  }
});

document.querySelectorAll('.driver-main input:not([type="hidden"]),.driver-main textarea').forEach(input => {
  input.addEventListener('input', () => input.setAttribute('data-driver-dirty','true'));
});

document.querySelectorAll('[data-trip-create-form]').forEach(form=>form.addEventListener('submit',()=>{
  if(!form.checkValidity())return;
  const button=form.querySelector('button[type="submit"]');button.disabled=true;button.textContent='Створюємо рейс…';
}));
window.addEventListener('pageshow',()=>document.querySelectorAll('[data-trip-create-form] button[type="submit"]').forEach(button=>{button.disabled=false;button.textContent='Створити рейс';}));

// Update only document indicators; leave filters, scroll and expanded routes untouched.
const liveTtnRows = document.querySelectorAll('[data-trip-ttn-id]');
if (liveTtnRows.length) {
  let pendingTtn = false;
  async function updateTripTtn() {
    if (pendingTtn || document.visibilityState !== 'visible') return;
    pendingTtn = true;
    try {
      const url = new URL(window.location.href);
      url.search = '';
      url.searchParams.set('ttn_live', '1');
      url.searchParams.set('ids', [...liveTtnRows].map(row => row.dataset.tripTtnId).join(','));
      const response = await fetch(url, {cache:'no-store', headers:{Accept:'application/json'}, signal:AbortSignal.timeout(10000)});
      if (!response.ok || !response.headers.get('content-type')?.includes('application/json')) return;
      const data = await response.json();
      liveTtnRows.forEach(row => {
        const items = data.trips[row.dataset.tripTtnId];
        if (!items) return;
        const key = JSON.stringify(items);
        if (row.dataset.ttnState === key) return;
        const fragment = document.createDocumentFragment();
        items.forEach(item => {
          const container = document.createElement('div'); container.className = 'trip-ttn-list-row';
          const label = document.createElement('span'); label.textContent = item.label;
          const status = document.createElement('strong'); status.className = 'fleet-ttn-state is-' + item.state; status.textContent = item.text;
          container.append(label, status); fragment.append(container);
        });
        if (!items.length) { const empty = document.createElement('span'); empty.className = 'muted'; empty.textContent = 'Немає активних точок'; fragment.append(empty); }
        row.replaceChildren(fragment); row.dataset.ttnState = key;
      });
    } catch (_) { /* Retry on the next interval. */ }
    finally { pendingTtn = false; }
  }
  setInterval(updateTripTtn, 5000);
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') updateTripTtn(); });
}

// Driver TTN notices are kept independently for each dispatcher.
const ttnNoticeBox=document.querySelector('[data-ttn-notifications]');
if(ttnNoticeBox){
  const badge=ttnNoticeBox.querySelector('[data-ttn-count]'), list=ttnNoticeBox.querySelector('[data-ttn-items]');
  let pending=false, previousCount=null, previousItems='';
  async function refreshTtnNotices(){
    if(pending || document.visibilityState!=='visible') return;
    pending=true;
    try{
      const response=await fetch(ttnNoticeBox.dataset.url,{cache:'no-store',headers:{Accept:'application/json'},signal:AbortSignal.timeout(10000)});
      if(response.status===403){ttnNoticeBox.hidden=true;return}
      if(!response.ok || !response.headers.get('content-type')?.includes('application/json')) throw new Error('unavailable');
      const data=await response.json();
      badge.hidden=!data.unread;badge.textContent=data.unread>99?'99+':String(data.unread);
      if(previousCount!==null && data.unread>previousCount){
        const toast=document.createElement('div');toast.className='ttn-notice-toast';toast.setAttribute('role','status');toast.textContent='Нове повідомлення від водія · перегляньте';document.body.append(toast);setTimeout(()=>toast.remove(),7000);
      }
      previousCount=data.unread;
      const itemsKey=JSON.stringify(data.items);
      if(itemsKey===previousItems) return;
      previousItems=itemsKey;
      const fragment=document.createDocumentFragment();
      data.items.forEach(item=>{
        const link=document.createElement('a');link.href=item.url;link.className='ttn-notice'+(item.read?'':' is-unread');
        const title=document.createElement('strong');title.textContent=item.kind+' · рейс №'+item.trip;
        const info=document.createElement('span');info.textContent=item.driver+' · '+item.vehicle;
        const time=document.createElement('time');time.textContent=item.time;
        link.append(title,info,time);
        if(!item.read){
          let reading=false,read=false;
          const markViewed=async()=>{
            if(reading||read)return;
            reading=true;
            try{
              const token=document.querySelector('input[name=csrfmiddlewaretoken]')?.value;
              const result=await fetch(ttnNoticeBox.dataset.readUrl,{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded','X-CSRFToken':token},body:new URLSearchParams({id:item.id}),signal:AbortSignal.timeout(5000)});
              if(result.ok){read=true;link.classList.remove('is-unread');previousCount=Math.max(0,(previousCount||0)-1);badge.textContent=previousCount>99?'99+':String(previousCount);badge.hidden=!previousCount;previousItems='';}
            }catch(_){}finally{reading=false}
          };
          link.addEventListener('mouseenter',markViewed);
          link.addEventListener('focus',markViewed);
        }
        if(!item.read) link.addEventListener('click',async event=>{
          event.preventDefault();
          try{const token=document.querySelector('input[name=csrfmiddlewaretoken]')?.value;await fetch(ttnNoticeBox.dataset.readUrl,{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded','X-CSRFToken':token},body:new URLSearchParams({id:item.id}),signal:AbortSignal.timeout(5000)});}catch(_){}
          window.location.assign(item.url);
        });
        fragment.append(link);
      });
      if(!data.items.length){const empty=document.createElement('p');empty.textContent='Нових повідомлень поки немає.';fragment.append(empty)}
      list.replaceChildren(fragment);
    }catch(_){
      previousItems='';
      const error=document.createElement('p');error.setAttribute('role','status');
      error.textContent='Не вдалося оновити повідомлення. Спробуємо ще раз автоматично.';
      list.replaceChildren(error);
    }finally{pending=false}
  }
  refreshTtnNotices();setInterval(refreshTtnNotices,5000);
  ttnNoticeBox.addEventListener('toggle',()=>{if(ttnNoticeBox.open)refreshTtnNotices()});
  document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')refreshTtnNotices()});
}

// Keep the current driver in view without scrolling the rest of the page.
(() => {
  const menu = document.querySelector('.report-driver-menu');
  const list = menu?.querySelector('.report-driver-list');
  const selected = list?.querySelector('a[aria-current="page"]');
  if (!menu || !list || !selected) return;
  function revealSelected() {
    if (!menu.open) return;
    requestAnimationFrame(() => {
      const listBox = list.getBoundingClientRect();
      const itemBox = selected.getBoundingClientRect();
      if (itemBox.top < listBox.top || itemBox.bottom > listBox.bottom) {
        list.scrollTop += itemBox.top - listBox.top - (list.clientHeight - itemBox.height) / 2;
      }
    });
  }
  menu.addEventListener('toggle', revealSelected);
  revealSelected();
})();
