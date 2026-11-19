(function(root){
  const valid=(lat,lon)=>Number.isFinite(lat)&&Number.isFinite(lon)&&lat>=-90&&lat<=90&&lon>=-180&&lon<=180;
  const pair=text=>{const match=text.trim().match(/^(-?\d+(?:\.\d+)?)\s*[,;]\s*(-?\d+(?:\.\d+)?)$/);return match&&valid(+match[1],+match[2])?[+match[1],+match[2]]:null;};
  function parseCoordinates(text){
    const direct=pair(text);if(direct)return direct;
    try{
      const url=new URL(text);
      if(url.protocol!=='https:'||!(url.hostname==='google.com'||url.hostname.endsWith('.google.com')||url.hostname==='maps.google.com.ua'||url.hostname==='www.google.com.ua'||url.hostname==='openstreetmap.org'||url.hostname==='www.openstreetmap.org'))return null;
      for(const key of ['destination','query','q']){const result=pair(url.searchParams.get(key)||'');if(result)return result;}
      if(url.searchParams.has('mlat')&&url.searchParams.has('mlon'))return pair(url.searchParams.get('mlat')+','+url.searchParams.get('mlon'));
      const matches=[...decodeURIComponent(url.href).matchAll(/!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)/g)];
      if(matches.length===1)return pair(matches[0][1]+','+matches[0][2]);
    }catch(error){}
    return null;
  }
  const api={parse:parseCoordinates,valid};
  if(typeof module==='object'&&module.exports)module.exports=api;else root.MakoshCoordinates=api;
})(typeof window!=='undefined'?window:this);
