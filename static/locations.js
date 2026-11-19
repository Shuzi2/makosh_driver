(() => {
  const dialog=document.querySelector('[data-location-dialog]');
  const {valid,parse:parseCoordinates}=window.MakoshCoordinates;
  const icon=()=>L.divIcon({className:'location-pin',iconSize:[22,22],iconAnchor:[11,11]});
  const tile=(map,url,attribution)=>L.tileLayer(url,{maxZoom:19,attribution,referrerPolicy:'strict-origin-when-cross-origin'}).addTo(map);
  document.querySelectorAll('[data-location-preview]').forEach(details=>details.addEventListener('toggle',()=>{
    if(!details.open||details.dataset.loaded||!window.L)return;
    const lat=+details.dataset.lat,lon=+details.dataset.lon;if(!valid(lat,lon))return;
    const map=L.map(details.querySelector('[data-preview-map]'),{scrollWheelZoom:false}).setView([lat,lon],16);
    tile(map,details.dataset.tileUrl,details.dataset.attribution);L.marker([lat,lon],{icon:icon(),keyboard:false}).addTo(map);details.dataset.loaded='1';
  }));
  if(!dialog)return;
  let active=null,map=null,marker=null,selected=null,searchController=null,selectedName='';
  const message=dialog.querySelector('[data-location-message]');
  const confirm=dialog.querySelector('[data-location-confirm]');
  const query=dialog.querySelector('[data-location-query]');
  const results=dialog.querySelector('[data-location-results]');
  const coordinateLabel=dialog.querySelector('[data-location-coordinate-label]');
  const selectionTitle=dialog.querySelector('[data-location-selection-title]');
  const center=dialog.querySelector('[data-location-center]');
  const searchHelp=dialog.querySelector('[data-location-search-help]');
  function resetCandidate(){selected=null;selectedName='';confirm.disabled=true;center.disabled=true;selectionTitle.textContent='Місце ще не вибране';coordinateLabel.textContent='Вибір точки необов’язковий';if(marker&&map){map.removeLayer(marker);marker=null;}results.querySelectorAll('button').forEach(button=>button.classList.remove('selected'));}
  function cancelSearch(){searchController?.abort();searchController=null;dialog.querySelector('[data-location-search]').disabled=false;}
  query.addEventListener('input',()=>{cancelSearch();results.replaceChildren();searchHelp.hidden=false;message.textContent='';resetCandidate();});
  dialog.querySelector('[data-location-paste]').addEventListener('input',()=>{cancelSearch();results.replaceChildren();resetCandidate();});
  function select(lat,lon,zoom=true){
    if(!valid(lat,lon))return;
    selected=[+lat.toFixed(6),+lon.toFixed(6)];
    if(map){if(!marker){marker=L.marker(selected,{icon:icon(),draggable:true,title:'Точка маршруту',alt:'Точка маршруту'}).addTo(map);marker.on('dragend',()=>{const point=marker.getLatLng().wrap();selectedName='В’їзд, позначений на карті';select(point.lat,point.lng,false);});}else marker.setLatLng(selected);if(zoom)map.setView(selected,16);}
    selectionTitle.textContent=selectedName||'Мітку встановлено вручну';
    coordinateLabel.textContent=selected[0].toFixed(6)+', '+selected[1].toFixed(6);confirm.disabled=false;center.disabled=false;
  }
  function markField(widget){
    const lat=document.getElementById(widget.dataset.latId),lon=document.getElementById(widget.dataset.lonId);
    const a=lat.value.trim(),b=lon.value.trim();
    const added=Boolean(a&&b&&valid(+a,+b));
    widget.querySelector('[data-location-status]').textContent=added?'Точку додано. Водій бачитиме карту й кнопку маршруту.':'Можна залишити порожнім. Карта з’явиться у водія лише після вибору точки.';
    const presence=widget.querySelector('[data-location-presence]');if(presence)presence.textContent=added?'Додано':'Необов’язково';
  }
  function bindLocationField(widget){
    if(widget.dataset.locationBound)return;widget.dataset.locationBound='1';
    const address=document.getElementById(widget.dataset.addressId),lat=document.getElementById(widget.dataset.latId),lon=document.getElementById(widget.dataset.lonId),verified=document.getElementById(widget.dataset.confirmId);
    if(!address||!lat||!lon||!verified)return;
    address.addEventListener('input',()=>{lat.value='';lon.value='';verified.value='';markField(widget);});
    [lat,lon].forEach(input=>input.addEventListener('input',()=>{verified.value='True';markField(widget);}));
    widget.querySelector('[data-pick-location]').addEventListener('click',()=>{
      active={widget,address,lat,lon,verified};resetCandidate();results.replaceChildren();searchHelp.hidden=false;message.textContent='';query.value=address.value;dialog.querySelector('[data-location-paste]').value='';dialog.querySelector('.location-advanced').open=false;
      dialog.querySelector('#location-dialog-title').textContent=widget.dataset.stop==='point'?'Точка маршруту':widget.dataset.stop==='origin'?'Точка завантаження':'Точка розвантаження';
      dialog.showModal();
      if(window.L){
        if(!map){map=L.map(dialog.querySelector('[data-location-map]')).setView([49,31],6);const tiles=tile(map,dialog.dataset.tileUrl,dialog.dataset.attribution);tiles.on('tileerror',()=>{message.textContent='Підкладка карти недоступна. Координати можна ввести вручну.';});map.on('click',event=>{const point=event.latlng.wrap();selectedName='В’їзд, позначений на карті';select(point.lat,point.lng,false);});}
        map.invalidateSize();if(marker){map.removeLayer(marker);marker=null;}map.setView([49,31],6);
      }else message.textContent='Карта не завантажилась. Введіть координати вручну нижче.';
      if(lat.value!==''&&lon.value!==''&&valid(+lat.value,+lon.value)){selectedName=address.value||'Збережене місце';select(+lat.value,+lon.value);}
    });
    markField(widget);
  }
  document.querySelectorAll('[data-location-field]').forEach(bindLocationField);
  document.addEventListener('route:added',event=>event.target.querySelectorAll('[data-location-field]').forEach(bindLocationField));
  async function search(){
    if(dialog.querySelector('[data-location-search]').disabled)return;
    resetCandidate();
    if(query.value.trim().length<3){message.textContent='Укажіть населений пункт, область і адресу.';return;}
    searchController?.abort();searchController=new AbortController();const controller=searchController;
    const timer=setTimeout(()=>controller.abort(),12000);const button=dialog.querySelector('[data-location-search]');button.disabled=true;results.replaceChildren();message.textContent='Шукаємо варіанти…';
    try{
      const response=await fetch(dialog.dataset.searchUrl+'?q='+encodeURIComponent(query.value.trim()),{credentials:'same-origin',headers:{Accept:'application/json'},signal:controller.signal});
      if(response.redirected||!response.headers.get('content-type')?.includes('application/json'))throw new Error('Увійдіть у кабінет знову або виберіть точку вручну.');
      const data=await response.json();if(!response.ok)throw new Error(data.error||'Пошук недоступний.');
      if(controller!==searchController||!dialog.open)return;
      if(!Array.isArray(data.results))throw new Error('Пошук карти зараз недоступний. Можна вибрати точку вручну.');
      message.textContent=data.results.length?'Оберіть правильний населений пункт і перевірте мітку.':'Нічого не знайдено. Додайте область або виберіть точку вручну.';
      searchHelp.hidden=Boolean(data.results.length);
      data.results.forEach((item,index)=>{const option=document.createElement('button');option.type='button';const number=document.createElement('span');number.className='location-result-number';number.textContent=index+1;const label=document.createElement('span');label.textContent=item.label;option.append(number,label);option.addEventListener('click',()=>{results.querySelectorAll('button').forEach(button=>button.classList.remove('selected'));option.classList.add('selected');selectedName=item.label;select(+item.lat,+item.lon);message.textContent='Перевірте область і мітку. За потреби пересуньте її до потрібного в’їзду.';});results.append(option);});
    }catch(error){if(dialog.open&&controller===searchController)message.textContent=error.name==='AbortError'?'Немає відповіді від пошуку. Можна ввести координати або вибрати місце вручну.':error.message;}
    finally{clearTimeout(timer);if(controller===searchController)button.disabled=false;}
  }
  dialog.querySelector('[data-location-search]').addEventListener('click',search);
  query.addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();search();}});
  dialog.querySelector('[data-location-apply-coords]').addEventListener('click',()=>{
    const point=parseCoordinates(dialog.querySelector('[data-location-paste]').value);
    if(!point){message.textContent='Потрібні координати мітки, наприклад 49.233083, 28.468217. Центр карти та короткі посилання не визначають точку.';return;}
    selectedName='Точка за координатами';select(...point);message.textContent='Перевірте мітку та підтвердьте точку.';
  });
  confirm.addEventListener('click',()=>{if(!active||!selected)return;active.lat.value=selected[0].toFixed(6);active.lon.value=selected[1].toFixed(6);active.verified.value='True';markField(active.widget);dialog.close();});
  center.addEventListener('click',()=>{if(map&&selected)map.setView(selected,17);});
  dialog.querySelector('[data-location-clear]').addEventListener('click',()=>{if(!active)return;active.lat.value='';active.lon.value='';active.verified.value='';markField(active.widget);dialog.close();});
  dialog.querySelector('[data-location-close]').addEventListener('click',()=>dialog.close());
  dialog.addEventListener('close',()=>{cancelSearch();active?.widget.querySelector('[data-pick-location]').focus();});
})();
