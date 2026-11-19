// Read only: new assignments appear automatically, but never discard local work.
(() => {
  const dashboard=document.querySelector('[data-driver-dashboard]');
  if(!dashboard)return;
  const note=document.querySelector('[data-driver-live-note]');
  const connection=document.querySelector('[data-driver-connection]');
  const setConnection=(state,message)=>{if(connection){connection.dataset.state=state;connection.textContent=message;}};
  const cancellationWarning=document.querySelector('[data-driver-cancel-warning]');
  const routeWarning=document.querySelector('[data-route-update-warning]');
  let checking=false;
  const safeToRefresh=()=>{
    const number=document.querySelector('[data-driver-upload] input[name="ttn_number"]:not([type="hidden"])');
    const numberChanged=number && number.value!==number.defaultValue;
    const selects=[...(document.querySelectorAll?.('[data-driver-upload] select')||[])];
    const choiceChanged=selects.some(select=>{const options=[...select.options];const initial=options.find(option=>option.defaultSelected)||options[0];return initial&&select.value!==initial.value;});
    const editingUpload=!!document.activeElement?.closest?.('[data-driver-upload]');
    return document.visibilityState==='visible'&&!window.driverHasPendingAction&&!numberChanged&&!choiceChanged&&!editingUpload&&!document.querySelector('[data-driver-dirty],form[aria-busy="true"],dialog[open]');
  };
  const show=message=>{if(note){note.hidden=!message;if(note.textContent!==message)note.textContent=message;}};
  async function check(){
    if(checking||document.visibilityState!=='visible')return;
    checking=true;
    const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),10000);
    try{
      const response=await fetch(dashboard.dataset.updatesUrl,{cache:'no-store',credentials:'same-origin',headers:{Accept:'application/json'},signal:controller.signal});
      if(response.redirected){setConnection('unavailable','Увійдіть знову, щоб оновлювати дані');show('Оновлення призупинено. Увійдіть у кабінет знову.');return;}
      if(!response.ok||!response.headers.get('content-type')?.includes('application/json'))throw new Error('unavailable');
      const data=await response.json();
      if(typeof data.version!=='string')throw new Error('invalid');
      setConnection('connected','Зв’язок із сервером підтверджено');
      if(cancellationWarning)cancellationWarning.hidden=!data.cancellation_pending||safeToRefresh();
      if(routeWarning)routeWarning.hidden=!data.route_change_pending||safeToRefresh();
      // Recheck after the request: the driver might have started typing meanwhile.
      if(dashboard.dataset.cancellationsOnly){
        if(data.cancellation_pending&&safeToRefresh()){window.location.replace('/');return;}
        if(data.version!==dashboard.dataset.version){
          if(safeToRefresh())window.location.reload();
          else show(data.assignment_pending?'Є нове завдання від логіста. Завершіть роботу з файлами, щоб переглянути його.':'Документи оновилися. Завершіть роботу з вибраними файлами, щоб оновити список.');
          return;
        }
      } else if(data.version!==dashboard.dataset.version&&safeToRefresh()){
        if(window.location.pathname==='/')window.location.reload();else window.location.replace('/');
        return;
      } else if(data.version!==dashboard.dataset.version&&data.assignment_pending){
        show('Є нове завдання від логіста. Завершіть поточну дію, щоб переглянути його.');
        return;
      }
      show('');
    }catch(error){setConnection('unavailable','Зв’язок із сервером недоступний');show('Зв’язку із сервером немає. Нові рейси з’являться після відновлення зв’язку.');}
    finally{clearTimeout(timer);checking=false;}
  }
  setInterval(check,5000);
  document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')check();else setConnection('checking','Перевірка зв’язку призупинена');});
  window.addEventListener('offline',()=>setConnection('unavailable','Зв’язок із сервером недоступний'));
  window.addEventListener('online',check);
})();
