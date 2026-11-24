(() => {
  const fleet=document.querySelector('#fleet-cards');
  if(!fleet)return;
  const notice=document.querySelector('[data-copy-feedback]');let timer;
  const textFor=card=>{
    const d=card.dataset;
    const specs=(prefix,label)=>{
      const length=d[`copy${prefix}Length`],width=d[`copy${prefix}Width`],height=d[`copy${prefix}Height`],empty=d[`copy${prefix}EmptyWeight`],max=d[`copy${prefix}MaxWeight`];
      return [`Габарити ${label} (Д × Ш × В), мм: ${[length,width,height].map(v=>v||'—').join(' × ')}`,`Маса ${label} (без вантажу / максимальна), кг: ${empty||'—'} / ${max||'—'}`];
    };
    return [`Перевізник: ${fleet.dataset.copyCarrier||'Не вказано'}`,`Марка: ${d.copyBrand||'Не вказано'}`,`Тягач: ${d.copyTractor||'Не вказано'}`,...specs('Tractor','тягача'),`Причіп: ${d.copyTrailer||'Не закріплено'}`,...(d.copyTrailer?specs('Trailer','причепа'):[]),`Тип: ${d.copyKind||'Не вказано'}`,`Водій: ${d.copyDriver||'Не закріплений'}`,`Телефон: ${d.copyPhone||'Не вказано'}`,`Посвідчення водія: ${d.copyLicense||'Не вказано'}`,`Паспорт: ${d.copyPassport||'Не вказано'}`,`Місце зберігання авто: ${fleet.dataset.copyStorage||'Не вказано'}`].join('\n');
  };
  function manualCopy(text){
    const dialog=document.createElement('dialog');dialog.className='fleet-copy-dialog';dialog.setAttribute('aria-labelledby','fleet-copy-title');
    dialog.innerHTML='<h2 id="fleet-copy-title">Дані автомобіля</h2><p>Не вдалося скопіювати автоматично. Виділіть і скопіюйте текст нижче.</p><textarea readonly aria-label="Дані автомобіля"></textarea><button type="button" class="btn" data-close>Закрити</button>';
    const field=dialog.querySelector('textarea');field.value=text;document.body.append(dialog);
    dialog.querySelector('[data-close]').addEventListener('click',()=>dialog.close());dialog.addEventListener('close',()=>dialog.remove(),{once:true});
    dialog.showModal();field.focus();field.select();
  }
  fleet.addEventListener('click',async event=>{
    const button=event.target.closest('[data-copy-vehicle]');if(!button)return;
    const card=button.closest('[data-vehicle-id]');if(!card)return;
    const text=textFor(card);
    try{
      if(!navigator.clipboard?.writeText)throw new Error('Clipboard unavailable');
      await navigator.clipboard.writeText(text);
      const menu=button.closest('details');if(menu)menu.open=false;
      if(notice){clearTimeout(timer);notice.textContent='Дані авто скопійовано';notice.hidden=false;timer=setTimeout(()=>{notice.hidden=true},3500)}
    }catch(_){manualCopy(text)}
  });
})();
