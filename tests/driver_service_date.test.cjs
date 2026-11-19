const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('static/driver_service_date.js','utf8');
function fixture(input){
  let click;
  const button={addEventListener(name,fn){click=fn}};
  const document={querySelector(selector){return selector==='#id_due_date'?input:button}};
  vm.runInNewContext(source,{document});
  return ()=>click();
}
test('opens the native date picker directly from the button',()=>{
  let opened=0;fixture({showPicker(){opened++}})();assert.equal(opened,1);
});
test('falls back to the date input when the picker is unavailable',()=>{
  let focused=0,clicked=0;fixture({focus(){focused++},click(){clicked++}})();
  assert.equal(focused,1);assert.equal(clicked,1);
});
