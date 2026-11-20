const test=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const source=require('node:fs').readFileSync('static/staff-mobile.js','utf8');
test('mobile navigation opens and closes on repeat tap, backdrop and escape',()=>{
  const listeners={}, buttonListeners={}, backListeners={}, attrs={'aria-expanded':'false'}, classes=new Set();
  const button={addEventListener:(k,v)=>buttonListeners[k]=v,setAttribute:(k,v)=>attrs[k]=v,getAttribute:k=>attrs[k],focus(){}};
  const backdrop={hidden:true,addEventListener:(k,v)=>backListeners[k]=v};
  const aside={querySelector:()=>({focus(){}})};
  const document={body:{classList:{add:k=>classes.add(k),toggle:(k,v)=>v?classes.add(k):classes.delete(k)}},querySelector:s=>s.includes('backdrop')?backdrop:s.includes('navigation')?aside:button,addEventListener:(k,v)=>listeners[k]=v};
  vm.runInNewContext(source,{document,window:{addEventListener(){}}});
  buttonListeners.click();assert.equal(attrs['aria-expanded'],'true');assert.equal(backdrop.hidden,false);assert.ok(classes.has('staff-menu-open'));
  buttonListeners.click();assert.equal(attrs['aria-expanded'],'false');assert.equal(backdrop.hidden,true);
  buttonListeners.click();backListeners.click();assert.equal(attrs['aria-expanded'],'false');
  buttonListeners.click();listeners.keydown({key:'Escape'});assert.equal(attrs['aria-expanded'],'false');
});
