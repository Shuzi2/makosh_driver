// Retain final totals while the driver adds a missing TTN or loses connection.
(() => {
  const form=document.querySelector('[data-driver-finish-form]');
  if(!form)return;
  const key='makosh-finish-draft-'+document.body.dataset.driverUser+'-'+form.dataset.syncUrl;
  const fields=[...form.querySelectorAll('[name="empty_distance_km"], [name="loaded_distance_km"], [name="fuel_rate"], [name="actual_tons"], [name^="unload_weight_"]')];
  try{
    const saved=JSON.parse(localStorage.getItem(key)||'{}');
    fields.forEach(field=>{if(!field.value&&typeof saved[field.name]==='string')field.value=saved[field.name];});
  }catch(error){}
  function save(){
    form.setAttribute('data-driver-dirty','true');
    try{localStorage.setItem(key,JSON.stringify(Object.fromEntries(fields.map(field=>[field.name,field.value]))));}catch(error){}
  }
  fields.forEach(field=>field.addEventListener('input',save));
})();
