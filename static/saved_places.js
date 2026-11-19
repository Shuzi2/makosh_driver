(() => {
  function bind(card){
    const picker=card.querySelector('[data-saved-place-picker]');
    if(!picker||picker.dataset.bound)return;picker.dataset.bound='1';
    const query=picker.querySelector('[data-place-query]'),results=picker.querySelector('[data-place-results]'),message=picker.querySelector('[data-place-message]');
    const field=name=>card.querySelector('[name$="-'+name+'"]');
    let timer,controller,sequence=0;
    async function search(){
      controller?.abort();controller=new AbortController();const own=++sequence;
      const url=new URL(picker.dataset.searchUrl,location.origin);url.searchParams.set('q',query.value);url.searchParams.set('kind',field('kind')?.value||'');
      try{
        const response=await fetch(url,{credentials:'same-origin',headers:{Accept:'application/json'},signal:controller.signal});
        if(!response.ok)throw new Error('search');const data=await response.json();if(own!==sequence)return;
        results.replaceChildren();
        data.results.forEach(place=>{
          const button=document.createElement('button');button.type='button';
          const title=document.createElement('b');title.textContent=place.name;
          const address=document.createElement('small');address.textContent=place.address;button.append(title,address);
          button.addEventListener('click',()=>{
            card.querySelector('[data-settlement-field]').dispatchEvent(new CustomEvent('saved-place:select',{detail:place}));
            ['lat','lon','contact_name','contact_phone','contact_phone_alt','notes'].forEach(name=>{const input=field(name);if(input){input.value=place[name]??'';input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));}});
            const extra=field('contact_phone_alt')?.closest('details');if(extra)extra.open=Boolean(place.contact_phone_alt);
            const confirmed=field('point_confirmed');if(confirmed)confirmed.value='true';
            picker.open=false;message.textContent='Підставлено: '+place.name;
            field('address').dispatchEvent(new Event('change',{bubbles:true}));
          });results.append(button);
        });message.textContent=data.results.length?'Оберіть місце. Контакти й заїзд підставляться разом з адресою.':'Збережених місць за цим запитом немає.';
      }catch(error){if(error.name!=='AbortError')message.textContent='Не вдалося завантажити місця. Можна вказати адресу нижче.';}
    }
    picker.addEventListener('toggle',()=>{if(picker.open)search();});
    query.addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(search,200);});
  }
  document.querySelectorAll('[data-stop-form]').forEach(bind);
  document.addEventListener('route:added',event=>bind(event.target));
})();
