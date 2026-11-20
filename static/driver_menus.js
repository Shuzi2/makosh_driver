// Keep both controls predictable on touch, mouse, and keyboard.
(() => {
  const menus=[...document.querySelectorAll('.driver-notifications, .driver-settings')];
  menus.forEach(menu=>{
    const summary=menu.querySelector('summary');
    if(!summary)return;
    summary.addEventListener('click',event=>{
      event.preventDefault();
      const opening=!menu.open;
      menus.forEach(other=>{if(other!==menu)other.open=false;});
      menu.open=opening;
    });
  });
  document.addEventListener('click',event=>menus.forEach(menu=>{if(!menu.contains(event.target))menu.open=false;}));
  document.addEventListener('keydown',event=>{
    if(event.key!=='Escape')return;
    menus.forEach(menu=>{if(menu.open){const focused=menu.contains(document.activeElement);menu.open=false;if(focused)menu.querySelector('summary')?.focus();}});
  });
})();
