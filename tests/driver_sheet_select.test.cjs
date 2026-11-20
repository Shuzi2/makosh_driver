const test=require('node:test');
const assert=require('node:assert/strict');
const {parseNumber,summarize}=require('../static/driver_sheet_select.js');
test('parses Ukrainian formatted positive and negative amounts without accepting dates or text',()=>{
  assert.equal(parseNumber('1 234,50'),1234.5);
  assert.equal(parseNumber('-5,25'),-5.25);
  assert.equal(parseNumber('0,00'),0);
  assert.equal(parseNumber('20.11.2026'),null);
  assert.equal(parseNumber('—'),null);
  assert.equal(parseNumber('10 км'),null);
});
test('totals only numeric selected cells and identifies mixed columns',()=>{
  assert.deepEqual(summarize([{text:'100,50',column:2},{text:'-20,25',column:2},{text:'—',column:2}]),{sum:80.25,count:2,mixed:false});
  assert.equal(summarize([{text:'1',column:2},{text:'3',column:4}]).mixed,true);
});
