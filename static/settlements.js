(()=>{
 let counter=0;
 let alignmentFrame=null;
 function alignAddresses(){
  if(alignmentFrame!==null)return;
  alignmentFrame=requestAnimationFrame(()=>{
   alignmentFrame=null;
   const cards=[...document.querySelectorAll('.create-route-editor [data-route-forms]>.create-point')].filter(card=>!card.hidden);
   cards.forEach(card=>{card.querySelector('.create-point-address').style.minHeight='';});
   if(window.innerWidth<=1150)return;
   for(let index=0;index+1<cards.length;index+=2){
    const addresses=cards.slice(index,index+2).map(card=>card.querySelector('.create-point-address'));
    const height=Math.max(...addresses.map(address=>address.querySelector('.settlement-field').getBoundingClientRect().height));
    addresses.forEach(address=>{address.style.minHeight=Math.ceil(height)+'px';});
   }
  });
 }
 const observer=window.ResizeObserver?new ResizeObserver(alignAddresses):null;
 window.addEventListener('resize',alignAddresses);
 document.querySelector('[data-route-editor]')?.addEventListener('click',alignAddresses);
 function bind(widget){
  if(widget.dataset.settlementBound)return;
  const input=widget.querySelector('input[name$="-address"]');if(!input||input.disabled)return;
  widget.dataset.settlementBound='1';
  observer?.observe(widget);alignAddresses();
  const results=widget.querySelector('[data-settlement-results]'),selection=widget.querySelector('[data-settlement-selection]'),message=widget.querySelector('[data-settlement-message]'),manual=widget.querySelector('[data-settlement-manual]'),reset=widget.querySelector('[data-settlement-reset]');
  const areaFields=widget.querySelector('[data-settlement-areas]');
  const metadata=name=>widget.querySelector('[name$="-'+name+'"]');
  results.id='settlement-results-'+(++counter);results.setAttribute('role','listbox');
  input.setAttribute('role','combobox');input.setAttribute('aria-autocomplete','list');input.setAttribute('aria-controls',results.id);input.setAttribute('aria-expanded','false');
  let mode=metadata('settlement_code')?.value?'selected':input.value.trim()?'saved':'search',timer=null,controller=null,sequence=0,items=[],active=-1,selectedAddress=input.value;
  const text=(tag,value,className)=>{const node=document.createElement(tag);node.textContent=value;if(className)node.className=className;return node;};
  function close(){clearTimeout(timer);controller?.abort();controller=null;sequence++;results.hidden=true;input.setAttribute('aria-expanded','false');input.removeAttribute('aria-activedescendant');active=-1;}
  function choose(item){
   close();mode='selected';selectedAddress=item.address;input.value=item.address;selection.replaceChildren();
   ['region','district','community'].forEach(name=>{metadata(name).value=item[name];metadata(name).required=false;});
   metadata('settlement_code').value=item.code;metadata('settlement_name').value=item.title;areaFields.hidden=true;
   selection.append(text('b',item.kind+' '+item.name));
   const rows=document.createElement('dl');
   [['Область',item.region],['Район',item.district],['Громада (ОТГ)',item.community]].forEach(([label,value])=>{rows.append(text('dt',label),text('dd',value||'Не вказано в кодифікаторі'));});
   selection.append(rows);selection.hidden=false;reset.hidden=false;manual.hidden=false;
   message.textContent='Можна дописати вулицю, склад або адресу заїзду.';
   input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));input.focus();
   alignAddresses();
  }
  widget.addEventListener('saved-place:select',event=>{
   const place=event.detail;
   choose({address:place.address,region:place.region,district:place.district,community:place.community,
    code:place.settlement_code,title:place.settlement_name,kind:'Місце',name:place.name});
  });
  async function search(query){
   controller?.abort();controller=new AbortController();const current=++sequence;
   message.textContent='Шукаємо населений пункт…';
   try{
    const url=new URL(widget.dataset.searchUrl,window.location.origin);url.searchParams.set('q',query);
    const response=await fetch(url,{signal:controller.signal,credentials:'same-origin',headers:{Accept:'application/json'}});
    if(!response.ok||!response.headers.get('content-type')?.includes('application/json'))throw new Error('search');
    const data=await response.json();if(current!==sequence||input.value.trim()!==query||mode!=='search')return;
    items=data.results;results.replaceChildren();active=-1;
    if(!items.length){close();message.textContent='Не знайдено. Уточніть назву або введіть адресу вручну.';return;}
    items.forEach((item,index)=>{
     const button=document.createElement('button');button.type='button';button.id=results.id+'-'+index;button.setAttribute('role','option');button.setAttribute('aria-selected','false');button.tabIndex=-1;
     button.append(text('b',item.title),text('small',[item.region,item.district,item.community].filter(Boolean).join(' · ')));
     button.addEventListener('click',()=>choose(item));results.append(button);
    });
    const fieldRect=widget.getBoundingClientRect(),inputRect=input.getBoundingClientRect();results.style.top=(inputRect.bottom-fieldRect.top+5)+'px';
    results.hidden=false;input.setAttribute('aria-expanded','true');message.textContent=data.more?'Є інші збіги. Додайте область, район або громаду до назви.':'Оберіть населений пункт зі списку або введіть вручну.';
   }catch(error){if(error.name!=='AbortError'&&current===sequence){close();message.textContent='Пошук недоступний. Адресу можна ввести вручну.';}}
  }
  input.addEventListener('input',()=>{
   if(mode==='manual')return;
   if(mode==='selected'&&input.value.startsWith(selectedAddress))return;
   mode='search';selection.hidden=true;close();const query=input.value.trim();
   ['settlement_code','settlement_name','region','district','community'].forEach(name=>{metadata(name).value='';});areaFields.hidden=false;
   if(query.length<2){message.textContent='Введіть щонайменше дві літери назви.';return;}
   timer=setTimeout(()=>search(query),250);
  });
  input.addEventListener('keydown',event=>{
   if(event.key==='Escape'){close();return;}
   if(results.hidden)return;
   if(event.key==='ArrowDown'||event.key==='ArrowUp'){
    event.preventDefault();active=(active+(event.key==='ArrowDown'?1:-1)+items.length)%items.length;
    [...results.children].forEach((button,index)=>{button.setAttribute('aria-selected',String(index===active));if(index===active){input.setAttribute('aria-activedescendant',button.id);button.scrollIntoView({block:'nearest'});}});
   }else if(event.key==='Enter'){event.preventDefault();choose(items[active<0?0:active]);}
   else if(event.key==='Tab')close();
  });
  manual.addEventListener('click',()=>{close();mode='manual';selection.hidden=true;manual.hidden=true;reset.hidden=false;metadata('settlement_code').value='';metadata('settlement_name').value='';areaFields.hidden=false;areaFields.open=true;['region','district','community'].forEach(name=>{metadata(name).required=true;});message.textContent='Заповніть область, район і громаду — водій побачить їх в адресі.';input.focus();alignAddresses();});
  reset.addEventListener('click',()=>{close();mode='search';selection.hidden=true;manual.hidden=false;reset.hidden=true;message.textContent='Введіть назву населеного пункту. Можна додати область або громаду.';input.focus();input.select();});
  document.addEventListener('click',event=>{if(!widget.contains(event.target))close();});
 }
 document.querySelectorAll('[data-settlement-field]').forEach(bind);
 document.addEventListener('route:added',event=>event.target.querySelectorAll('[data-settlement-field]').forEach(bind));
})();
