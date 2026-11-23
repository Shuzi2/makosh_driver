(() => {
  const dialog=document.querySelector('[data-document-viewer]');if(!dialog||!dialog.showModal)return;
  const find=name=>dialog.querySelector('[data-viewer-'+name+']');
  let documents=[],index=0,opener=null,controller=null,sequence=0;
  const canvas=find('canvas'),message=find('message'),thumbs=find('thumbnails');
  function show(){
    const item=documents[index];if(!item)return;
    canvas.replaceChildren();canvas.classList.remove('is-zoomed');find('zoom').textContent='Збільшити ＋';
    find('title').textContent=item.title;find('subtitle').textContent=[item.place,item.number?'ТТН '+item.number:'',item.name].filter(Boolean).join(' · ');
    find('counter').textContent=(index+1)+' / '+documents.length;find('prev').disabled=index===0;find('next').disabled=index===documents.length-1;
    find('download').href=item.download;find('zoom').hidden=item.type==='application/pdf'||item.type==='application/octet-stream';message.textContent='';
    if(item.type==='application/pdf'){
      const frame=document.createElement('iframe');frame.src=item.url;frame.title=item.title;frame.className='document-pdf';canvas.append(frame);
      const fallback=document.createElement('a');fallback.href=item.url;fallback.target='_blank';fallback.rel='noopener';fallback.textContent='Відкрити PDF окремо ↗';canvas.append(fallback);
    }else if(item.type==='application/octet-stream'){
      message.textContent='Файл збережено; попередній перегляд недоступний. Завантажте його для відкриття.';
    }else{
      const img=document.createElement('img');img.src=item.url;img.alt=item.title;img.addEventListener('error',()=>{if(canvas.contains(img))message.textContent='Не вдалося відкрити фото. Перевірте інтернет або завантажте оригінал.';});canvas.append(img);
    }
    [...thumbs.children].forEach((button,i)=>{button.classList.toggle('selected',i===index);button.setAttribute('aria-current',String(i===index));});
  }
  document.addEventListener('click',async event=>{
    const link=event.target.closest('a[data-document-preview]');if(!link)return;event.preventDefault();opener=link;
    controller?.abort();controller=new AbortController();const own=++sequence;
    if(!dialog.open)dialog.showModal();documents=[];canvas.replaceChildren();thumbs.replaceChildren();message.textContent='Відкриваємо документ…';find('title').textContent='Документ';
    ['prev','next','zoom'].forEach(name=>find(name).disabled=true);find('download').removeAttribute('href');find('subtitle').textContent='';find('counter').textContent='';find('trip').textContent='';
    try{
      const url=new URL(link.href,location.origin);url.searchParams.set('format','json');
      const response=await fetch(url,{credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'},signal:controller.signal});
      if(response.redirected||!response.ok)throw new Error('preview');const data=await response.json();if(own!==sequence||!dialog.open)return;
      documents=data.documents;index=Math.max(0,documents.findIndex(item=>item.id===data.selected));find('trip').textContent='Рейс №'+data.trip;
      documents.forEach((item,i)=>{
        const button=document.createElement('button');button.type='button';button.title=item.title+' · '+item.place;
        if(item.type==='application/pdf'||item.type==='application/octet-stream'){const label=document.createElement('span');label.textContent=item.type==='application/pdf'?'PDF':'ФАЙЛ';button.append(label);}
        else{const img=document.createElement('img');img.src=item.url;img.loading='lazy';img.alt=item.title;button.append(img);}
        const number=document.createElement('small');number.textContent=String(i+1);button.append(number);button.addEventListener('click',()=>{index=i;show();});thumbs.append(button);
      });find('zoom').disabled=false;show();
    }catch(error){if(error.name!=='AbortError'){message.textContent='Не вдалося відкрити документи. Перевірте зв’язок.';const fallback=document.createElement('a');fallback.href=link.href;fallback.textContent='Відкрити окремою сторінкою →';canvas.append(fallback);}}
  });
  find('prev').addEventListener('click',()=>{if(index>0){index--;show();}});find('next').addEventListener('click',()=>{if(index<documents.length-1){index++;show();}});
  find('zoom').addEventListener('click',()=>{canvas.classList.toggle('is-zoomed');find('zoom').textContent=canvas.classList.contains('is-zoomed')?'Зменшити −':'Збільшити ＋';});
  find('close').addEventListener('click',()=>dialog.close());
  dialog.addEventListener('keydown',event=>{if(event.key==='ArrowLeft'&&index>0){index--;show();}if(event.key==='ArrowRight'&&index<documents.length-1){index++;show();}});
  dialog.addEventListener('close',()=>{sequence++;controller?.abort();canvas.replaceChildren();thumbs.replaceChildren();opener?.focus();});
})();
