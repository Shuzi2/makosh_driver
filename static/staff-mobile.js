(() => {
  const button=document.querySelector('[data-staff-menu]');
  const aside=document.querySelector('#staff-navigation');
  const backdrop=document.querySelector('[data-staff-menu-backdrop]');
  if(!button||!aside||!backdrop)return;
  document.body.classList.add('staff-menu-ready');
  const setOpen=open=>{
    document.body.classList.toggle('staff-menu-open',open);
    button.setAttribute('aria-expanded',String(open));
    button.setAttribute('aria-label',open?'Закрити меню':'Відкрити меню');
    backdrop.hidden=!open;
    if(open) aside.querySelector('a')?.focus();
    else button.focus();
  };
  button.addEventListener('click',()=>setOpen(button.getAttribute('aria-expanded')!=='true'));
  backdrop.addEventListener('click',()=>setOpen(false));
  document.addEventListener('keydown',event=>{if(event.key==='Escape'&&button.getAttribute('aria-expanded')==='true')setOpen(false)});
  window.addEventListener('resize',()=>{if(window.innerWidth>760&&button.getAttribute('aria-expanded')==='true')setOpen(false)});
})();
