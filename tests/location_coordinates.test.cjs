const test=require('node:test');
const assert=require('node:assert/strict');
const coordinates=require('../static/location_coordinates.js');
test('accepts coordinates of one explicit pin and preserves latitude-longitude order',()=>{
  for(const value of ['49.233083, 28.468217','49.233083;28.468217','https://www.google.com/maps/search/?api=1&query=49.233083%2C28.468217','https://www.google.com/maps/place/example/data=!8m2!3d49.233083!4d28.468217']){
    assert.deepEqual(coordinates.parse(value),[49.233083,28.468217]);
  }
});
test('does not mistake map center, shortened URL, multiple pins or malicious host for a destination',()=>{
  for(const value of ['https://www.google.com/maps/@49.233083,28.468217,16z','https://maps.app.goo.gl/example','https://google.com.evil.example/?q=49.233083,28.468217','javascript:alert(1)','91,28','49,181','NaN,28','https://www.google.com/maps/data=!3d49!4d28!3d50!4d30'])assert.equal(coordinates.parse(value),null);
});
