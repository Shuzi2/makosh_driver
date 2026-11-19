(() => {
  const user=document.currentScript?.dataset.driverUser;
  if(!user)return;
  const key='mtruck-driver-theme:'+user, root=document.documentElement;
  let theme='light';
  try{if(localStorage.getItem(key)==='dark')theme='dark'}catch(_){}
  const apply=()=>{
    root.dataset.driverTheme=theme;
    const button=document.querySelector('[data-driver-theme-toggle]');
    if(button){button.setAttribute('aria-pressed',String(theme==='dark'));button.textContent=theme==='dark'?'☀ Світлий режим':'☾ Нічний режим'}
  };
  apply();
  document.addEventListener('DOMContentLoaded',()=>{
    apply();
    document.querySelector('[data-driver-theme-toggle]')?.addEventListener('click',()=>{
      theme=theme==='dark'?'light':'dark';apply();
      try{localStorage.setItem(key,theme)}catch(_){}
    });
  });
})();
