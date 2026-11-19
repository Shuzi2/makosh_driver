const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('static/driver_theme.js','utf8');
function setup(initial={}, user='7'){
  const state={...initial},root={dataset:{}},button={attrs:{},setAttribute(k,v){this.attrs[k]=v},addEventListener(k,fn){this[k]=fn}};
  const document={documentElement:root,currentScript:{dataset:{driverUser:user}},querySelector(){return button},addEventListener(name,fn){if(name==='DOMContentLoaded')this.ready=fn}};
  const localStorage={getItem(k){return state[k]||null},setItem(k,v){state[k]=v}};
  vm.runInNewContext(source,{document,localStorage});document.ready();
  return {state,root,button};
}
test('driver can enable night mode and restore the light mode',()=>{
  const app=setup();assert.equal(app.root.dataset.driverTheme,'light');assert.equal(app.button.attrs['aria-pressed'],'false');
  app.button.click();assert.equal(app.root.dataset.driverTheme,'dark');assert.equal(app.button.attrs['aria-pressed'],'true');
  assert.equal(app.state['mtruck-driver-theme:7'],'dark');app.button.click();assert.equal(app.root.dataset.driverTheme,'light');
});
test('theme is restored separately for each driver',()=>{
  const data={'mtruck-driver-theme:7':'dark'};
  assert.equal(setup(data,'7').root.dataset.driverTheme,'dark');
  assert.equal(setup(data,'8').root.dataset.driverTheme,'light');
});
