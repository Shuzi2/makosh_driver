(() => {
  const input=document.querySelector('[data-settlement-search]');
  if(!input)return;
  const list=document.getElementById(input.getAttribute('list'));
  const status=document.getElementById('acceptance-search-status');
  let timer,controller,sequence=0;
  input.addEventListener('input',()=>{
    clearTimeout(timer);controller?.abort();const query=input.value.trim();const current=++sequence;
    if(query.length<2 || query.includes(',')){list.replaceChildren();return;}
    timer=setTimeout(async()=>{
      controller=new AbortController();status.textContent='Шукаємо населений пункт…';
      try{
        const url=new URL(input.dataset.settlementSearch,window.location.origin);url.searchParams.set('q',query);
        const response=await fetch(url,{credentials:'same-origin',signal:controller.signal,headers:{Accept:'application/json'}});
        if(!response.ok)throw new Error('search');
        const data=await response.json();if(current!==sequence)return;
        list.replaceChildren(...data.results.map(item=>{const option=document.createElement('option');option.value=item.address;return option;}));
        status.textContent=data.results.length?'Виберіть населений пункт і допишіть місце стоянки.':'Населений пункт не знайдено. Можна вказати місце вручну.';
      }catch(error){if(error.name!=='AbortError' && current===sequence)status.textContent='Пошук недоступний. Можна вказати місце вручну.';}
    },250);
  });
})();
