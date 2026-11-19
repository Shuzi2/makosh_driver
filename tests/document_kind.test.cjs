const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
function fixture(){
  const kind={value:'ttn',addEventListener(name,fn){this.change=fn;}};
  const title={},hint={},picker={},submit={dataset:{}},field={disabled:false,value:'ТТН-2'};
  const numbers={hidden:false,querySelectorAll:()=>[field]};
  const card={querySelector:s=>({'[name="kind"]':kind,'[data-document-title]':title,'[data-document-instruction]':hint,'[data-document-picker-title]':picker,'button[type="submit"]':submit,'[data-ttn-fields]':numbers}[s]||null)};
  vm.runInNewContext(fs.readFileSync('static/document_kind.js','utf8'),{document:{querySelectorAll:()=>[card]}});
  return {kind,title,hint,picker,submit,numbers,field,select(value){kind.value=value;kind.change();}};
}
test('each selected type is reflected in heading instructions and send button',()=>{
  const app=fixture();
  for(const [kind,title,word] of [['ttn','Фото ТТН із завантаження','ТТН'],['signed_ttn','Фото ТТН з підписом','підписану ТТН'],['weight','Фото вагового талона','ваговий талон'],['other','Фото іншого документа','інший документ']]){
    app.select(kind);assert.equal(app.title.textContent,title);
    assert.ok(app.submit.textContent.includes(word));
    assert.equal(app.submit.textContent,app.submit.dataset.uploadLabel);
    if(kind==='weight'||kind==='other')assert.ok(!app.hint.textContent.includes('ТТН'));
  }
});
test('weight and other uploads do not require a TTN number; switching back keeps the selected number',()=>{
  const app=fixture();
  app.select('weight');assert.equal(app.numbers.hidden,true);assert.equal(app.field.disabled,true);
  app.select('other');assert.equal(app.field.disabled,true);
  app.select('signed_ttn');assert.equal(app.numbers.hidden,false);assert.equal(app.field.disabled,false);
  assert.equal(app.field.value,'ТТН-2');
});
