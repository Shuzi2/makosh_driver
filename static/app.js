const search=document.querySelector('#fleet-search');if(search){search.addEventListener('input',()=>{const query=search.value.toLocaleLowerCase('uk');document.querySelectorAll('.searchable').forEach(card=>{card.hidden=!card.textContent.toLocaleLowerCase('uk').includes(query);});});}
// Do not cache private pages or files in a service worker.
