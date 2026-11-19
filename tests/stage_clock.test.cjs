const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
test('stage clock advances without reload and follows the recorded timestamp after a stage change',()=>{
  let now=Date.parse('2026-10-22T15:20:00Z');
  const clock={dataset:{stageSince:'2026-10-22T12:00:00Z'},textContent:''};
  const intervals=[];
  class ClockDate extends Date{static now(){return now;}}
  const document={querySelector(){return null;},querySelectorAll(selector){return selector==='[data-stage-since]'?[clock]:[];},addEventListener(){}};
  vm.runInNewContext(fs.readFileSync('static/app.js','utf8'),{document,window:{addEventListener(){}},Date:ClockDate,setInterval(fn,ms){intervals.push({fn,ms});}});
  assert.equal(clock.textContent,'3 год 20 хв');
  assert.equal(intervals[0].ms,60000);
  now+=60000;intervals[0].fn();assert.equal(clock.textContent,'3 год 21 хв');
  clock.dataset.stageSince=new Date(now).toISOString();intervals[0].fn();assert.equal(clock.textContent,'щойно');
});
