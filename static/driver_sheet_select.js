(() => {
  function parseNumber(value) {
    const text=String(value ?? '').trim().replace(/[\s\u00a0\u202f]/g,'').replace(',', '.');
    if(!/^[+-]?(?:\d+(?:\.\d+)?|\.\d+)$/.test(text))return null;
    const result=Number(text);
    return Number.isFinite(result)?result:null;
  }
  function summarize(items) {
    const valid=items.map(item=>({...item,value:parseNumber(item.text)})).filter(item=>item.value!==null);
    return {sum:valid.reduce((total,item)=>total+item.value,0),count:valid.length,mixed:new Set(valid.map(item=>item.column)).size>1};
  }
  if(typeof module!=='undefined' && module.exports)module.exports={parseNumber,summarize};
  if(typeof document==='undefined')return;
  const table=document.querySelector('.driver-sheet'), output=document.querySelector('[data-sheet-selection]');
  if(!table||!output)return;
  const status=output.querySelector('[data-selection-status]'), clear=output.querySelector('[data-selection-clear]');
  const selected=new Set();let dragging=false,anchor=null,additive=false,base=new Set();
  const eligible=cell=>cell && cell.tagName==='TD' && cell.closest('tr[data-sheet-row]') && (cell.cellIndex>=2&&cell.cellIndex<=13||cell.cellIndex>=18&&cell.cellIndex<=27);
  const cellText=cell=>cell.querySelector('.sheet-cell-button span')?.textContent ?? cell.textContent;
  function update(){
    const result=summarize([...selected].map(cell=>({text:cellText(cell),column:cell.cellIndex})));
    clear.hidden=!selected.size;
    if(!result.count){status.textContent=selected.size?'У вибраних клітинках немає чисел.':'Виділіть числові клітинки мишею, щоб побачити суму.';return;}
    status.textContent=`Виділено: ${result.count} · Сума: ${new Intl.NumberFormat('uk-UA',{maximumFractionDigits:2,minimumFractionDigits:2}).format(result.sum)}${result.mixed?' · Різні колонки: перевірте одиниці виміру.':''}`;
  }
  function setSelection(cells){selected.forEach(cell=>cell.classList.remove('sheet-selected'));selected.clear();cells.forEach(cell=>{selected.add(cell);cell.classList.add('sheet-selected')});update();}
  function range(first,last){
    const rows=[...table.tBodies[0].querySelectorAll('tr[data-sheet-row]')].filter(row=>!row.hidden);
    const a=rows.indexOf(first.parentElement),b=rows.indexOf(last.parentElement);if(a<0||b<0)return [];
    const cells=[];for(let r=Math.min(a,b);r<=Math.max(a,b);r++)for(let c=Math.min(first.cellIndex,last.cellIndex);c<=Math.max(first.cellIndex,last.cellIndex);c++){const cell=rows[r].cells[c];if(eligible(cell))cells.push(cell)}return cells;
  }
  table.addEventListener('pointerdown',event=>{
    if(event.pointerType==='touch'||event.button!==0||event.detail>1||event.target.closest('input,textarea,select,a,details'))return;
    const cell=event.target.closest('td');if(!eligible(cell))return;
    dragging=true;anchor=cell;additive=event.ctrlKey||event.metaKey;base=additive?new Set(selected):new Set();
    if(additive&&base.has(cell)){base.delete(cell);setSelection(base);dragging=false;return;}
    setSelection(new Set([...base,cell]));
  });
  table.addEventListener('pointerover',event=>{
    if(!dragging||!anchor)return;const cell=event.target.closest('td');if(!eligible(cell)||cell===anchor)return;
    window.getSelection()?.removeAllRanges();setSelection(new Set([...base,...range(anchor,cell)]));
  });
  document.addEventListener('pointerup',()=>{dragging=false;anchor=null;});
  document.addEventListener('pointercancel',()=>{dragging=false;anchor=null;});
  clear.addEventListener('click',()=>setSelection([]));
  new MutationObserver(()=>{if(selected.size)update();}).observe(table.tBodies[0],{childList:true,characterData:true,subtree:true});
})();
