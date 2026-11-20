// Explicit buttons avoid platform-specific disclosure behavior on mobile Safari.
(() => {
  const menus=[...document.querySelectorAll('.driver-notifications, .driver-settings')];
  function setOpen(menu,open){
    const trigger=menu.querySelector('[data-driver-menu-trigger]');
    const panel=menu.querySelector('[data-driver-menu-panel]');
    if(!trigger||!panel)return;
    menu.dataset.open=open?'true':'false';
    trigger.setAttribute('aria-expanded',open?'true':'false');
    panel.hidden=!open;
    if(open)menu.dispatchEvent(new Event('driver-menu-open'));
  }
  menus.forEach(menu=>{
    const trigger=menu.querySelector('[data-driver-menu-trigger]');
    if(!trigger)return;
    setOpen(menu,false);
    trigger.addEventListener('click',()=>{
      const opening=menu.dataset.open!=='true';
      menus.forEach(other=>{if(other!==menu)setOpen(other,false)});
      setOpen(menu,opening);
    });
  });
  document.addEventListener('click',event=>menus.forEach(menu=>{if(!menu.contains(event.target))setOpen(menu,false)}));
  document.addEventListener('keydown',event=>{
    if(event.key!=='Escape')return;
    menus.forEach(menu=>{if(menu.dataset.open==='true'){const focused=menu.contains(document.activeElement);setOpen(menu,false);if(focused)menu.querySelector('[data-driver-menu-trigger]')?.focus();}});
  });
})();
