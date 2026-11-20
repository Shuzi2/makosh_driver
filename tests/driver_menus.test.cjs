const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('static/driver_menus.js','utf8');

function fixture(){
  const handlers={};
  function menu(){
    const listeners={};
    const summary={addEventListener(type,fn){listeners[type]=fn},focus(){this.focused=true}};
    return {open:false,summary,querySelector(){return summary},contains(target){return target===summary},click(){listeners.click({preventDefault(){},target:summary})}};
  }
  const bell=menu(),settings=menu();
  const document={querySelectorAll(){return [bell,settings]},addEventListener(type,fn){handlers[type]=fn},activeElement:null};
  vm.runInNewContext(source,{document});
  return {bell,settings,outside(){handlers.click({target:{}})},escape(){handlers.keydown({key:'Escape'})}};
}
test('both menus open and close with a second tap',()=>{
  const {bell,settings}=fixture();
  for(const menu of [bell,settings]){menu.click();assert.equal(menu.open,true);menu.click();assert.equal(menu.open,false)}
});
test('opening one menu closes the other and outside tap closes both',()=>{
  const {bell,settings,outside}=fixture();bell.click();settings.click();assert.equal(bell.open,false);assert.equal(settings.open,true);outside();assert.equal(settings.open,false);
});
