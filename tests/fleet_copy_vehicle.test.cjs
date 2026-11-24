const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

test('vehicle copy includes carrier and storage address for every vehicle',async()=>{
  let handler,copied;
  const fleet={dataset:{copyCarrier:'МАКОШ МІНЕРАЛ 41157153',copyStorage:'Вінницька обл., м. Гнівань, вул. Промислова, 9'},addEventListener(type,fn){handler=fn}};
  const notice={hidden:true,textContent:''};
  const document={querySelector(selector){return selector==='#fleet-cards'?fleet:notice}};
  const navigator={clipboard:{writeText:async text=>{copied=text}}};
  vm.runInNewContext(fs.readFileSync('static/fleet-copy-vehicle.js','utf8'),{document,navigator,setTimeout(){},clearTimeout(){}});
  for(const plate of ['AB1234ME','AB5678ME']){
    const card={dataset:{copyBrand:'SCANIA',copyTractor:plate,copyTrailer:'AB9876XG',copyKind:'Тент',copyDriver:'Тест Водій',copyPhone:'+380501112234',copyLicense:'TEST 123456',copyPassport:'TEST 654321',copyTractorLength:'6000',copyTractorWidth:'2550',copyTractorHeight:'3850',copyTractorEmptyWeight:'8075',copyTractorMaxWeight:'19000',copyTrailerLength:'10500',copyTrailerWidth:'2500',copyTrailerHeight:'3800',copyTrailerEmptyWeight:'6600',copyTrailerMaxWeight:'42000'}};
    const button={closest(selector){return selector==='[data-vehicle-id]'?card:null}};
    await handler({target:{closest(){return button}}});
    assert.match(copied,/Перевізник: МАКОШ МІНЕРАЛ 41157153/);
    assert.match(copied,/Місце зберігання авто: Вінницька обл\., м\. Гнівань, вул\. Промислова, 9/);
    assert.ok(copied.includes(plate));
    assert.ok(copied.includes('Посвідчення водія: TEST 123456'));
    assert.ok(copied.includes('Паспорт: TEST 654321'));
    assert.ok(copied.includes('Габарити тягача (Д × Ш × В), мм: 6000 × 2550 × 3850'));
    assert.ok(copied.includes('Маса тягача (без вантажу / максимальна), кг: 8075 / 19000'));
    assert.ok(copied.includes('Габарити причепа (Д × Ш × В), мм: 10500 × 2500 × 3800'));
    assert.ok(copied.includes('Маса причепа (без вантажу / максимальна), кг: 6600 / 42000'));
  }
});
