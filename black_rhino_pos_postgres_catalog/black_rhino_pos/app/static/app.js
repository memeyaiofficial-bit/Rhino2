async function api(url,data){let r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});let j={};try{j=await r.json()}catch(e){}if(!r.ok){toast(j.detail||'Something went wrong.','danger');return {ok:false,...j}}return {ok:true,...j}}
function toast(msg,type='success'){let x=document.createElement('div');x.className='toast '+type;x.textContent=msg;Object.assign(x.style,{position:'fixed',right:'22px',bottom:'22px',padding:'12px 15px',background:type==='danger'?'#7f2e28':'#20201e',color:'#fff',borderRadius:'6px',zIndex:100,fontWeight:'700',boxShadow:'0 10px 30px rgba(0,0,0,.25)'});document.body.appendChild(x);setTimeout(()=>x.remove(),2600)}
function showModal(id){document.getElementById(id).classList.add('open')}
function hideModal(id){document.getElementById(id).classList.remove('open')}
document.addEventListener('click',e=>{if(e.target.classList.contains('modal'))e.target.classList.remove('open')})

if ('serviceWorker' in navigator) { window.addEventListener('load', () => navigator.serviceWorker.register('/service-worker.js').catch(() => {})); }
