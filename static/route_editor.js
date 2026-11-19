(()=>{
 const root=document.querySelector('[data-route-editor]');if(!root)return;
 const list=root.querySelector('[data-route-forms]'),total=root.querySelector('[name="stops-TOTAL_FORMS"]');
 const activeCards=()=>[...list.children].filter(card=>!card.querySelector('[name$="-DELETE"]')?.checked&&!card.classList.contains('route-point-cancelled'));
 const field=(card,name)=>card.querySelector('[name$="-'+name+'"]');
 const creating=root.hasAttribute('data-route-create'),removed=[];
 if(creating)[...list.children].forEach(card=>{if(field(card,'DELETE')?.checked)removed.push(card);});
 function updateOverview(){
  const overview=root.querySelector('[data-route-overview]');if(!overview)return;
  const cards=activeCards();let loaded=0,unloaded=0;
  cards.forEach(card=>{
   const amount=Number(field(card,'planned_tons')?.value.replace(',','.'));
   if(!Number.isFinite(amount)||amount<0)return;
   if(field(card,'kind')?.value==='load')loaded+=Math.round(amount*100);else unloaded+=Math.round(amount*100);
  });
  const tons=value=>(value/100).toLocaleString('uk-UA',{maximumFractionDigits:2})+' т';
  const simple=cards.length===2&&field(cards[0],'kind')?.value==='load'&&field(cards[1],'kind')?.value==='unload';
  overview.hidden=simple;overview.querySelector('[data-route-count]').textContent=cards.length;
  overview.querySelector('[data-route-load]').textContent=loaded?tons(loaded):'—';overview.querySelector('[data-route-unload]').textContent=unloaded?tons(unloaded):'—';
  const balance=overview.querySelector('[data-route-balance]');
  balance.textContent=!loaded&&!unloaded?'Укажіть планову вагу точок':loaded===unloaded?'Завантаження = розвантаження':loaded>unloaded?'Ще розподілити: '+tons(loaded-unloaded):'Розвантаження більше на '+tons(unloaded-loaded);
  balance.classList.toggle('is-balanced',Boolean(loaded)&&loaded===unloaded);
 }
 function refresh(){
  const cards=activeCards();
  const simple=cards.length===2&&field(cards[0],'kind')?.value==='load'&&field(cards[1],'kind')?.value==='unload';
  root.classList.toggle('is-simple-route',simple);
  const shared=creating&&simple;
  [...list.children].forEach(card=>{
   const inputs=card.querySelector('[data-cargo-inputs]'),summary=card.querySelector('[data-shared-cargo]');
   if(!inputs||!summary)return;
   const automatic=shared&&card===cards[1];
   if(automatic&&!card.dataset.sharedApplied){
    if(card.dataset.seenSeparate)card.sharedCargoBackup={cargo:field(card,'cargo').value,tons:field(card,'planned_tons').value,loadCargo:field(cards[0],'cargo').value,loadTons:field(cards[0],'planned_tons').value};
    card.dataset.sharedApplied='1';
   }else if(!automatic&&card.dataset.sharedApplied){
    const backup=card.sharedCargoBackup,load=cards.find(item=>field(item,'kind')?.value==='load');
    if(backup&&load&&backup.loadCargo===field(load,'cargo').value&&backup.loadTons===field(load,'planned_tons').value){field(card,'cargo').value=backup.cargo;field(card,'planned_tons').value=backup.tons;}
    delete card.dataset.sharedApplied;
   }
   if(!automatic&&cards.length>2)card.dataset.seenSeparate='1';
   inputs.hidden=automatic;summary.hidden=!automatic;
   ['cargo','planned_tons'].forEach(name=>{
    const input=field(card,name);
    if(automatic){
     if(input.required){input.dataset.sharedRequired='1';input.required=false;}
     input.value=field(cards[0],name).value;
    }else if(input.dataset.sharedRequired){input.required=true;delete input.dataset.sharedRequired;}
   });
   if(automatic){
    const cargo=field(cards[0],'cargo').value.trim(),tons=field(cards[0],'planned_tons').value;
    summary.querySelector('[data-shared-cargo-value]').textContent=[cargo,tons?tons.replace('.',',')+' т':''].filter(Boolean).join(' · ')||'Укажіть вантаж і вагу в завантаженні';
   }
  });
  let position=0;
  let previous=null;
  [...list.children].forEach(card=>{
   const deleted=card.querySelector('[name$="-DELETE"]')?.checked||card.classList.contains('route-point-cancelled');
   card.classList.toggle('route-marked-delete',Boolean(deleted));
   if(creating)card.hidden=Boolean(deleted);
   if(!deleted)position++;
   const order=card.querySelector('[name$="-ORDER"]');if(order)order.value=position;
   card.querySelector('[data-stop-number]').textContent=deleted?'×':position;
   const select=card.querySelector('[name$="-kind"]');card.querySelector('[data-stop-title]').textContent=select?.value==='load'?'Завантаження':'Розвантаження';
   card.dataset.stopKind=select?.value||'';
   const remove=card.querySelector('[data-remove-stop]');
   if(remove){
    remove.hidden=deleted||cards.length<=2;
    remove.disabled=cards.filter(item=>field(item,'kind')?.value===select?.value).length<=1;
    remove.title=remove.disabled?'Залиште хоча б одне завантаження та розвантаження':'Прибрати точку';
   }
   if(creating)card.querySelectorAll('[data-move-stop]').forEach(button=>{button.disabled=button.dataset.moveStop==='up'?cards[0]===card:cards.at(-1)===card;});
   const editRemove=card.querySelector('[data-edit-remove]');
   if(editRemove){
    editRemove.hidden=!deleted&&cards.length<=2;
    editRemove.disabled=!deleted&&(cards.length<=2||cards.filter(item=>field(item,'kind')?.value===select?.value).length<=1);
    editRemove.title=editRemove.disabled?'Залиште хоча б одне завантаження та розвантаження':'';
    editRemove.classList.toggle('is-restore',Boolean(deleted));
    editRemove.querySelector('[data-cancel-label]').textContent=deleted?'Повернути точку':field(card,'id')?.value?'Скасувати точку':'Прибрати точку';
    card.querySelector('[data-cancel-pending]').hidden=!deleted;
   }
   if(!creating)card.querySelectorAll('[data-move-stop]').forEach(button=>{
    const index=cards.indexOf(card),locked=card.hasAttribute('data-stop-locked');
    button.disabled=Boolean(deleted)||locked||(button.dataset.moveStop==='up'?(index<=0||cards[index-1].hasAttribute('data-stop-locked')):index===cards.length-1);
   });
   const preview=card.querySelector('[data-stop-address-preview]');if(preview)preview.textContent=field(card,'address')?.value|| (select?.value==='load'?'Звідки забрати вантаж':'Куди доставити вантаж');
   const copy=card.querySelector('[data-copy-stop]');if(copy)copy.hidden=deleted||!previous;
   if(!deleted)previous=card;
   card.querySelectorAll('[required]').forEach(input=>{if(deleted){input.dataset.wasRequired='1';input.required=false;}});
   if(!deleted)card.querySelectorAll('[data-was-required]').forEach(input=>{if(input.closest('[data-cargo-inputs]')?.hidden){input.dataset.sharedRequired='1';input.required=false;}else input.required=true;delete input.dataset.wasRequired;});
  });
  const undo=root.querySelector('[data-stop-undo]');if(undo)undo.hidden=!removed.length;
  updateOverview();
 }
 root.querySelectorAll('[data-add-stop]').forEach(button=>button.addEventListener('click',()=>{
  if(+total.value>=60)return;
  const fragment=root.querySelector('[data-stop-template]').content.cloneNode(true);
  const index=+total.value;
  fragment.querySelectorAll('*').forEach(element=>[...element.attributes].forEach(attr=>{if(attr.value.includes('__prefix__'))element.setAttribute(attr.name,attr.value.replaceAll('__prefix__',index));}));
  const card=fragment.querySelector('[data-stop-form]');card.querySelector('[name$="-kind"]').value=button.dataset.addStop;
  list.append(card);total.value=index+1;refresh();
  card.dispatchEvent(new Event('route:added',{bubbles:true}));card.querySelector('[name$="-address"]').focus();
 }));
 root.addEventListener('click',event=>{
  const editRemove=event.target.closest('[data-edit-remove]');
  if(editRemove){
   if(editRemove.disabled)return;
   const card=editRemove.closest('[data-stop-form]'),input=field(card,'DELETE');
   input.checked=!input.checked;refresh();editRemove.focus();return;
  }
  const remove=event.target.closest('[data-remove-stop]');
  if(remove){
   const card=remove.closest('[data-stop-form]'),cards=activeCards();
   if(cards.length<=2||cards.filter(item=>field(item,'kind').value===field(card,'kind').value).length<=1)return;
   field(card,'DELETE').checked=true;removed.push(card);refresh();
   root.querySelector('[data-restore-stop]').focus();return;
  }
  if(event.target.closest('[data-restore-stop]')){
   const card=removed.pop();if(card){field(card,'DELETE').checked=false;refresh();field(card,'address').focus();}return;
  }
  const copy=event.target.closest('[data-copy-stop]');
  if(copy){
   const card=copy.closest('[data-stop-form]'),cards=activeCards(),previous=cards[cards.indexOf(card)-1];
   if(previous){['cargo','planned_tons'].forEach(name=>{field(card,name).value=field(previous,name).value;});refresh();field(card,'planned_tons').focus();}
   return;
  }
  const button=event.target.closest('[data-move-stop]');if(!button)return;
  const card=button.closest('[data-stop-form]');
  if(creating||!button.disabled){
   const cards=activeCards(),index=cards.indexOf(card);
   if(button.dataset.moveStop==='up'&&index>0)list.insertBefore(card,cards[index-1]);
   if(button.dataset.moveStop==='down'&&index<cards.length-1)list.insertBefore(cards[index+1],card);
   refresh();return;
  }
 });
 root.addEventListener('input',refresh);root.addEventListener('change',refresh);
 // Browser validation must reveal optional fields before focusing them.
 root.addEventListener('invalid',event=>{let details=event.target.closest('details');while(details){details.open=true;details=details.parentElement.closest('details');}},true);
 refresh();
})();
