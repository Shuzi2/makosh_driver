// Keep the inbox current without replacing a comment or a focused action.
function attentionCanRefresh(root){
  if(root.contains(document.activeElement)||root.querySelector('details[open]'))return false;
  return ![...root.querySelectorAll('textarea')].some(input=>input.value!==input.defaultValue);
}
function attentionSetCount(count){
  document.querySelectorAll('[data-attention-count]').forEach(badge=>{badge.textContent=count;badge.title=`Активних питань: ${count}`;badge.hidden=!count;});
}
function attentionMarkupVersion(markup){
  // Django rotates the masked CSRF token on each render; it is not a data change.
  return markup.replace(/name="csrfmiddlewaretoken" value="[^"]*"/g,'name="csrfmiddlewaretoken"');
}
const attentionRoot=document.querySelector('[data-attention-inbox]');
const attentionScript=document.querySelector('[data-attention-count-url]');
let attentionLoading=false;
async function attentionRefresh(){
  if(attentionLoading||document.visibilityState!=='visible')return;
  attentionLoading=true;
  try{
    const url=new URL(attentionRoot?attentionRoot.dataset.liveUrl:attentionScript.dataset.attentionCountUrl,location.origin);
    if(attentionRoot)url.searchParams.set('live','1');
    const response=await fetch(url,{cache:'no-store',headers:{Accept:'application/json'},signal:AbortSignal.timeout(10000)});
    if(!response.ok||!response.headers.get('content-type')?.includes('application/json'))throw new Error('Unavailable');
    const data=await response.json();attentionSetCount(data.count);
    if(Number.isInteger(data.service_count))document.querySelectorAll('[data-service-count]').forEach(badge=>{badge.textContent=data.service_count;badge.hidden=!data.service_count;});
    const version=attentionRoot?attentionMarkupVersion(data.html):null;
    if(attentionRoot&&attentionCanRefresh(attentionRoot)&&attentionRoot.dataset.lastMarkup!==version){attentionRoot.innerHTML=data.html;attentionRoot.dataset.lastMarkup=version;}
    const error=document.querySelector('[data-attention-error]');if(error)error.hidden=true;
  }catch(error){const note=document.querySelector('[data-attention-error]');if(note)note.hidden=false;}
  finally{attentionLoading=false;}
}
if(attentionRoot)attentionRoot.dataset.lastMarkup=attentionMarkupVersion(attentionRoot.innerHTML);
if(attentionScript){setInterval(attentionRefresh,attentionRoot?5000:10000);document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')attentionRefresh();});}
document.addEventListener('submit',event=>{if(event.target.matches('[data-attention-action]')){if(event.target.dataset.submitting){event.preventDefault();return;}event.target.dataset.submitting='true';}});
