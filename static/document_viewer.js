(() => {
  const dialog=document.querySelector('[data-document-viewer]');if(!dialog||!dialog.showModal)return;
  const find=name=>dialog.querySelector('[data-viewer-'+name+']');
  let documents=[],index=0,opener=null,controller=null,sequence=0,scale=1,rotation=0;
  const canvas=find('canvas'),message=find('message'),thumbs=find('thumbnails');
  function updateImage(){
    const image=canvas.querySelector('.document-image-stage img'),stage=canvas.querySelector('.document-image-stage');
    if(!image||!stage||!image.naturalWidth||!image.naturalHeight)return;
    const turned=rotation%180!==0,w=image.naturalWidth,h=image.naturalHeight;
    const fit=Math.min((canvas.clientWidth-20)/(turned?h:w),(canvas.clientHeight-20)/(turned?w:h),1);
    const width=w*fit*scale,height=h*fit*scale;
    stage.style.width=(turned?height:width)+'px';stage.style.height=(turned?width:height)+'px';
    image.style.width=width+'px';image.style.height=height+'px';image.style.transform=`translate(-50%, -50%) rotate(${rotation}deg)`;
    find('scale').textContent=Math.round(scale*100)+'%';
    find('zoom-out').disabled=scale<=0.5;find('zoom').disabled=scale>=4;
  }
  function show(){
    const item=documents[index];if(!item)return;
    canvas.replaceChildren();scale=1;rotation=0;canvas.scrollTop=0;canvas.scrollLeft=0;
    find('title').textContent=item.title;find('subtitle').textContent=[item.place,item.number?'ТТН '+item.number:'',item.name].filter(Boolean).join(' · ');
    find('counter').textContent=(index+1)+' / '+documents.length;find('prev').disabled=index===0;find('next').disabled=index===documents.length-1;
    find('download').href=item.download;find('image-tools').hidden=item.type==='application/pdf'||item.type==='application/octet-stream';message.textContent='';
    if(item.type==='application/pdf'){
      const frame=document.createElement('iframe');frame.src=item.url;frame.title=item.title;frame.className='document-pdf';canvas.append(frame);
      const fallback=document.createElement('a');fallback.href=item.url;fallback.target='_blank';fallback.rel='noopener';fallback.textContent='Відкрити PDF окремо ↗';canvas.append(fallback);
    }else if(item.type==='application/octet-stream'){
      message.textContent='Файл збережено; попередній перегляд недоступний. Завантажте його для відкриття.';
    }else{
      const stage=document.createElement('div');stage.className='document-image-stage';const img=document.createElement('img');img.alt=item.title;img.addEventListener('load',updateImage);img.addEventListener('error',()=>{if(canvas.contains(img))message.textContent='Не вдалося відкрити фото. Перевірте інтернет або завантажте оригінал.';});stage.append(img);canvas.append(stage);img.src=item.url;
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
  find('zoom').addEventListener('click',()=>{scale=Math.min(4,Math.round((scale+0.25)*100)/100);updateImage();});
  find('zoom-out').addEventListener('click',()=>{scale=Math.max(0.5,Math.round((scale-0.25)*100)/100);updateImage();});
  find('rotate').addEventListener('click',()=>{rotation=(rotation+90)%360;updateImage();});
  find('reset').addEventListener('click',()=>{scale=1;rotation=0;canvas.scrollTop=0;canvas.scrollLeft=0;updateImage();});
  window.addEventListener('resize',()=>{if(dialog.open)updateImage();});
  find('close').addEventListener('click',()=>dialog.close());
  dialog.addEventListener('keydown',event=>{if(event.key==='ArrowLeft'&&index>0){index--;show();}if(event.key==='ArrowRight'&&index<documents.length-1){index++;show();}});
  dialog.addEventListener('close',()=>{sequence++;controller?.abort();canvas.replaceChildren();thumbs.replaceChildren();opener?.focus();});
})();
