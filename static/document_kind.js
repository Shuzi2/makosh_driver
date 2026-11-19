// Keep the visible upload instructions and submitted document type together.
(() => {
  const types={
    ttn:{name:'ТТН із завантаження',title:'Фото ТТН із завантаження',hint:'Сфотографуйте ТТН, яку отримали на завантаженні.',button:'Надіслати ТТН ↑'},
    signed_ttn:{name:'Підписана ТТН',title:'Фото ТТН з підписом',hint:'Сфотографуйте ТТН з підписом після розвантаження.',button:'Надіслати підписану ТТН ↑'},
    weight:{name:'Ваговий талон',title:'Фото вагового талона',hint:'Сфотографуйте ваговий талон так, щоб було чітко видно вагу.',button:'Надіслати ваговий талон ↑'},
    other:{name:'Інший документ',title:'Фото іншого документа',hint:'Сфотографуйте документ повністю, щоб текст було чітко видно.',button:'Надіслати інший документ ↑'},
  };
  document.querySelectorAll('.docs-upload-card').forEach(card=>{
    const kind=card.querySelector('[name="kind"]');
    if(!kind)return;
    const title=card.querySelector('[data-document-title]');
    const hint=card.querySelector('[data-document-instruction]');
    const picker=card.querySelector('[data-document-picker-title]');
    const submit=card.querySelector('button[type="submit"]');
    const numbers=card.querySelector('[data-ttn-fields]');
    function update(){
      const type=types[kind.value];
      title.textContent=type?.title||'Оберіть тип документа';
      hint.textContent=type?type.hint+' Якщо сторінок кілька — додайте всі.':'Оберіть, який документ хочете надіслати.';
      if(picker)picker.textContent=type?'Документ: '+type.name:'Змінити тип документа';
      submit.dataset.uploadLabel=type?.button||'Надіслати документи ↑';
      if(!card.querySelector('form[aria-busy="true"]'))submit.textContent=submit.dataset.uploadLabel;
      if(numbers){
        const isTtn=['ttn','signed_ttn'].includes(kind.value);
        numbers.hidden=!isTtn;
        numbers.querySelectorAll('input,select').forEach(field=>{field.disabled=!isTtn;});
      }
    }
    kind.addEventListener('change',update);
    update();
  });
})();
