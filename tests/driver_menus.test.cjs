const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('static/driver_menus.js','utf8');
function fixture(){
  const handlers={};
  function menu(){
    const listeners={},attrs={};
    const trigger={addEventListener(type,fn){listeners[type]=fn},setAttribute(name,value){attrs[name]=value},focus(){this.focused=true}};
    const panel={hidden:true};
    return {dataset:{},trigger,panel,querySelector(s){return s==='[data-driver-menu-trigger]'?trigger:panel},contains(target){return target===trigger},dispatchEvent(){},click(){listeners.click({preventDefault(){},target:trigger})},get expanded(){return attrs['aria-expanded']}};
  }
  const bell=menu(),settings=menu();
  const document={querySelectorAll(){return [bell,settings]},addEventListener(type,fn){handlers[type]=fn},activeElement:null};
  vm.runInNewContext(source,{document,Event});
  return {bell,settings,outside(){handlers.click({target:{}})},escape(){handlers.keydown({key:'Escape'})}};
}
test('both buttons open and close their panels on repeated tap',()=>{
  const {bell,settings}=fixture();
  for(const menu of [bell,settings]){menu.click();assert.equal(menu.panel.hidden,false);assert.equal(menu.expanded,'true');menu.click();assert.equal(menu.panel.hidden,true);assert.equal(menu.expanded,'false')}
});
test('opening another panel or tapping outside closes the previous panel',()=>{
  const {bell,settings,outside}=fixture();bell.click();settings.click();assert.equal(bell.panel.hidden,true);assert.equal(settings.panel.hidden,false);outside();assert.equal(settings.panel.hidden,true);
});
