const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

test('the print control calls the browser print dialog',()=>{
  let click,printed=0;
  const context={document:{querySelector:()=>({addEventListener:(event,handler)=>{if(event==='click')click=handler;}})},window:{print:()=>printed++}};
  vm.runInNewContext(fs.readFileSync('static/waybill_print.js','utf8'),context);
  assert.equal(typeof click,'function');click();assert.equal(printed,1);
});
