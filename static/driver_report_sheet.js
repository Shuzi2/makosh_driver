(() => {
 const table=document.querySelector('.driver-sheet'); if(!table)return;
 const targetHash=location.hash;
 if(/^#trip-\d+$/.test(targetHash)){
   const delay=matchMedia('(prefers-reduced-motion: reduce)').matches?0:3500;
   const target=document.getElementById(targetHash.slice(1));
   if(target&&delay)target.classList.add('trip-highlight');
   setTimeout(()=>{target?.classList.remove('trip-highlight');if(location.hash===targetHash)history.replaceState(history.state,'',location.pathname+location.search)},delay);
 }
 const viewport=document.querySelector('#driver-sheet-scroll'), top=document.querySelector('.sheet-top-scroll');
 if(viewport&&top){const track=top.firstElementChild;const resize=()=>{track.style.width=table.scrollWidth+'px';top.scrollLeft=viewport.scrollLeft};resize();window.addEventListener('resize',resize);if(window.ResizeObserver)new ResizeObserver(resize).observe(table);top.addEventListener('scroll',()=>viewport.scrollLeft=top.scrollLeft);viewport.addEventListener('scroll',()=>top.scrollLeft=viewport.scrollLeft);document.querySelectorAll('.sheet-jumps [data-column]').forEach(b=>b.addEventListener('click',()=>{const h=table.tHead.rows[0].cells[Number(b.dataset.column)];if(h)viewport.scrollLeft=h.getBoundingClientRect().left-table.getBoundingClientRect().left}));}
 const scrollKey='driver-sheet-position:'+location.pathname;try{const p=JSON.parse(sessionStorage.getItem(scrollKey));if(p){viewport.scrollLeft=p.x;window.scrollTo(0,p.y);sessionStorage.removeItem(scrollKey)}}catch(_){}
 const drafts=new Map(),save=document.querySelector('#sheet-save'),discard=document.querySelector('#sheet-discard'),status=document.querySelector('#sheet-edit-status');let busy=false,active=null;
 const clean=v=>String(v??'').trim().replace(/[\s\u00a0]/g,'').replace(',','.').replace(/^—$/,'');

 const headers=table.tHead.rows[0].cells;
 [3,4,5,6,7,9,10,11,12,13].forEach(i=>headers[i]?.classList.add('sheet-heading-edit'));
 [8,18,19,20,21,22,23,24,25,26].forEach(i=>headers[i]?.classList.add('sheet-heading-auto'));
 function paintResults(row){row.querySelectorAll('[data-result]').forEach(cell=>{const key=cell.dataset.result,raw=cell.textContent.trim().replace(/\s/g,'').replace(',','.');cell.classList.remove('is-positive','is-negative','is-empty');if(raw==='—'||raw===''){cell.classList.add('is-empty');return}const n=Number(raw);if(['profit','profit_per_km','margin'].includes(key)&&Number.isFinite(n)&&n!==0)cell.classList.add(n>0?'is-positive':'is-negative')})}
 table.querySelectorAll('tbody tr').forEach(paintResults);
 const previewTimers=new WeakMap(),previewVersions=new WeakMap();
 table.querySelectorAll('tr[data-preview-url]').forEach(row=>row.querySelectorAll('[data-result]').forEach(cell=>cell.dataset.initial=cell.textContent));
 function preview(row, currentCell=null, currentValue=null){
   if(!row?.dataset.previewUrl)return;
   clearTimeout(previewTimers.get(row)); const version=(previewVersions.get(row)||0)+1;previewVersions.set(row,version);
   const form=row.querySelector('.inline-editor form'), cells=[...row.querySelectorAll('[data-result]')];
   const entries=[...drafts].filter(([cell])=>cell.closest('tr')===row);
   const currentSource=currentCell?form.querySelector('[name$="-'+currentCell.dataset.field+'"]'):null;
   const changed=currentSource && clean(currentValue)!==clean(currentSource.value);
   if(!entries.length&&!changed){cells.forEach(cell=>{cell.textContent=cell.dataset.initial;cell.classList.remove('preview-pending');cell.title='Розрахункове значення'});return;}
   const data=new FormData(form);for(const [,draft] of entries)data.set(draft.source.name,draft.value);
   if(currentSource)data.set(currentSource.name,clean(currentValue));
   cells.forEach(cell=>{cell.classList.add('preview-pending');cell.title='Попередній розрахунок — збережіть зміни';});
   previewTimers.set(row,setTimeout(async()=>{try{
     const response=await fetch(row.dataset.previewUrl,{method:'POST',body:data,credentials:'same-origin'}),result=await response.json();
     if(previewVersions.get(row)!==version)return;
     if(!response.ok)throw new Error(result.error||'Перевірте значення');
     cells.forEach(cell=>cell.textContent=result.values[cell.dataset.result]??'—');paintResults(row);
   }catch(error){if(previewVersions.get(row)!==version)return;cells.forEach(cell=>{cell.textContent='—';cell.title=error.message});}},180));
 }
 function update(message){save.disabled=discard.disabled=busy||!drafts.size;status.textContent=message||(drafts.size?`Незбережених клітинок: ${drafts.size}`:'Змін немає')}
 function show(cell,value){const b=cell.querySelector('button');(b.querySelector('span')||b).textContent=value||'—'}
 function open(cell){if(!cell||busy||cell.querySelector('input'))return;if(active)active.blur();const b=cell.querySelector('button'),result=cell.classList.contains('sheet-result-editable'),form=cell.closest('tr').querySelector('.inline-editor form'),source=!result&&form?form.querySelector('[name$="-'+cell.dataset.field+'"]'):null;if(!b||(!result&&!source))return;const original=source?source.value:cell.dataset.value,input=document.createElement('input');input.type='text';input.inputMode='decimal';input.className='sheet-cell-input';input.setAttribute('aria-label',b.getAttribute('aria-label'));input.placeholder=result?'Порожньо — автоматично':'';input.value=drafts.has(cell)?drafts.get(cell).value:clean(original);b.hidden=true;cell.append(input);active=input;input.focus();input.select();let cancelled=false,finished=false;
 function finish(){if(finished)return;finished=true;const value=clean(input.value);input.remove();active=null;b.hidden=false;if(!cancelled){if(value===clean(original))drafts.delete(cell);else drafts.set(cell,{value,original:clean(original),source,form,result})}cell.classList.toggle('sheet-edit-pending',drafts.has(cell));show(cell,drafts.has(cell)?drafts.get(cell).value:original);preview(cell.closest('tr'));update()}
 input.addEventListener('input',()=>{if(clean(input.value)!==clean(original)){save.disabled=false;discard.disabled=false;status.textContent='Є незбережені зміни'}preview(cell.closest('tr'),cell,input.value)});input.addEventListener('blur',finish);input.addEventListener('keydown',e=>{if(e.key==='Escape'){e.preventDefault();cancelled=true;finish();b.focus();return}if(e.key==='Enter'||e.key==='Tab'){e.preventDefault();const cells=[...table.querySelectorAll('.sheet-editable,.sheet-result-editable')].filter(c=>!c.closest('tr').hidden),index=cells.indexOf(cell);finish();const next=cells[index+(e.shiftKey?-1:1)];if(next)open(next);else b.focus()}})}
 table.addEventListener('dblclick',e=>{const cell=e.target.closest('.sheet-editable,.sheet-result-editable');if(cell){e.preventDefault();open(cell)}});table.addEventListener('click',e=>{const b=e.target.closest('.sheet-cell-button');if(b&&(e.detail===0||matchMedia('(pointer: coarse)').matches))open(b.closest('.sheet-editable,.sheet-result-editable'))});
 discard.addEventListener('click',()=>{if(active)active.blur();const rows=new Set();for(const [cell,d] of drafts){rows.add(cell.closest('tr'));show(cell,d.original);cell.classList.remove('sheet-edit-pending')}drafts.clear();rows.forEach(row=>preview(row));update()});window.addEventListener('beforeunload',e=>{if(drafts.size&&!busy){e.preventDefault();e.returnValue=''}});
 save.addEventListener('click',async()=>{if(active)active.blur();if(!drafts.size||busy)return;busy=true;update('Зберігаємо зміни…');try{const forms=new Map();for(const [cell,d] of drafts)if(!d.result){if(!forms.has(d.form))forms.set(d.form,[]);forms.get(d.form).push([cell,d])}for(const [form,entries] of forms){const data=new FormData(form);for(const [,d] of entries)data.set(d.source.name,d.value);const response=await fetch(form.action,{method:'POST',body:data,credentials:'same-origin'}),html=await response.text(),page=new DOMParser().parseFromString(html,'text/html');if(!response.ok||!response.redirected)throw new Error(page.querySelector('.errorlist')?.textContent.trim()||'Не вдалося зберегти рядок. Перевірте значення.');const path=new URL(form.action).pathname,fresh=[...page.querySelectorAll('.inline-editor form')].find(f=>new URL(f.action).pathname===path),revision=fresh?.querySelector('[name$="-revision"]')?.value;if(revision===undefined)throw new Error('Рядок збережено, але версію не оновлено. Оновіть сторінку.');form.querySelector('[name$="-revision"]').value=revision;form.closest('tr').querySelectorAll('.sheet-result-editable').forEach(c=>c.dataset.revision=revision);for(const [cell,d] of entries){d.source.value=d.value;drafts.delete(cell);cell.classList.remove('sheet-edit-pending')}}for(const [cell,d] of [...drafts]){const data=new URLSearchParams({field:cell.dataset.field,value:d.value,revision:cell.dataset.revision,csrfmiddlewaretoken:document.querySelector('[name=csrfmiddlewaretoken]').value}),response=await fetch(cell.dataset.url,{method:'POST',body:data,credentials:'same-origin'}),result=await response.json();if(!response.ok)throw new Error(result.error||'Не вдалося зберегти клітинку.');cell.closest('tr').querySelectorAll('.sheet-result-editable').forEach(c=>c.dataset.revision=result.revision);cell.dataset.value=d.value;drafts.delete(cell);cell.classList.remove('sheet-edit-pending')}try{sessionStorage.setItem(scrollKey,JSON.stringify({x:viewport.scrollLeft,y:window.scrollY}))}catch(_){}location.reload()}catch(error){busy=false;update(error.message+' Збережені значення залишилися; інші можна виправити й зберегти повторно.')}});
})();
