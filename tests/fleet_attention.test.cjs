const test=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
function setup(){
 const document={activeElement:null,querySelector(){return null;},querySelectorAll(){return [];},addEventListener(){}};
 const context={document,setInterval(){}};
 vm.runInNewContext(fs.readFileSync('static/attention.js','utf8'),context);
 return {document,context};
}
test('inbox refresh protects focus, an open skip panel, and an unsent comment',()=>{
 const {document,context}=setup();let focused=false,open=false,inputs=[];
 const root={contains(){return focused;},querySelector(){return open?{}:null;},querySelectorAll(){return inputs;}};
 assert.equal(context.attentionCanRefresh(root),true);
 focused=true;assert.equal(context.attentionCanRefresh(root),false);
 focused=false;open=true;assert.equal(context.attentionCanRefresh(root),false);
 open=false;inputs=[{value:'draft',defaultValue:''}];assert.equal(context.attentionCanRefresh(root),false);
 inputs[0].value='';assert.equal(context.attentionCanRefresh(root),true);
});
test('sidebar count is updated and hidden when no active issues remain',()=>{
 const {document,context}=setup();const badge={};document.querySelectorAll=()=>[badge];
 context.attentionSetCount(3);assert.equal(badge.textContent,3);assert.equal(badge.hidden,false);
 context.attentionSetCount(0);assert.equal(badge.hidden,true);
});
test('changing a masked CSRF token does not trigger a new inbox render',()=>{
 const {context}=setup();
 const a='<input name="csrfmiddlewaretoken" value="first">same';
 const b='<input name="csrfmiddlewaretoken" value="second">same';
 assert.equal(context.attentionMarkupVersion(a),context.attentionMarkupVersion(b));
 assert.notEqual(context.attentionMarkupVersion(a),context.attentionMarkupVersion(b+'changed'));
});
