'use strict';
const $=(s,root=document)=>root.querySelector(s);
const $$=(s,root=document)=>[...root.querySelectorAll(s)];
const escapeHTML=(v='')=>String(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icons={folder:'<path d="M3 6a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z"/>',settings:'<path d="m10 3-1 3-3 1-2 3 1 3-1 3 2 3 3 1 1 2h4l1-2 3-1 2-3-1-3 1-3-2-3-3-1-1-3z"/><circle cx="12" cy="12" r="3"/>',refresh:'<path d="M20 10a8 8 0 0 0-14-5L3 8m0-5v5h5M4 14a8 8 0 0 0 14 5l3-3m0 5v-5h-5"/>',search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',film:'<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4"/>',tv:'<rect x="3" y="6" width="18" height="13" rx="2"/><path d="m8 2 4 4 4-4"/>',drive:'<rect x="3" y="13" width="18" height="7" rx="2"/><path d="M5 13 7 5h10l2 8M17 16.5h.01"/>',open:'<path d="M7 17 17 7M7 7h10v10"/>',trash:'<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',download:'<path d="M12 4v11m-5-5 5 5 5-5M5 20h14"/>',star:'<path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1 6.2-5.5-2.9-5.5 2.9 1-6.2L3 9.6l6.2-.9z"/>',pause:'<path d="M8 5v14M16 5v14"/>',play:'<path d="M7 5v14l11-7z"/>',close:'<path d="M6 6l12 12M18 6 6 18"/>',plus:'<path d="M12 5v14M5 12h14"/>',link:'<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',edit:'<path d="M4 20h4L19 9l-4-4L4 16zM14 6l4 4"/>',check:'<path d="m5 12 5 5 9-10"/>',heart:'<path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z"/>',locate:'<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/>',file:'<path d="M14 2H5v20h14V7zM14 2v6h5"/>'};
const icon=name=>`<svg viewBox="0 0 24 24" aria-hidden="true">${icons[name]||icons.file}</svg>`;
// Every page shares the sign-in dialog and the toast.
document.body.insertAdjacentHTML('beforeend',`<dialog id="login-dialog" class="sheet narrow"><form id="login-form" class="stack"><h2>登录 Plexus</h2><p id="login-message" class="muted"></p>
  <label id="login-server-field" hidden>Plexus 后台地址<input id="login-server" type="url" placeholder="https://…ts.net"></label>
  <input type="text" autocomplete="username" value="plexus" hidden><label>访问密码<input id="login-password" type="password" autocomplete="current-password" required></label>
  <button class="button" type="submit">登录</button><button id="login-change-server" class="text-button" type="button" hidden>更换后台地址</button></form></dialog><div id="toast" class="toast" role="status" hidden></div>`);
$$('[data-icon]').forEach(el=>el.innerHTML=icon(el.dataset.icon));
const number=n=>new Intl.NumberFormat('zh-CN').format(n||0);
const sizeText=n=>n>=1099511627776?`${(n/1099511627776).toFixed(2)} TB`:n>=1073741824?`${(n/1073741824).toFixed(1)} GB`:n>=1048576?`${(n/1048576).toFixed(0)} MB`:`${Math.round((n||0)/1024)} KB`;
const formatTime=n=>n?new Date(n*1000).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}):'';
const resolutionText=r=>({'4k':'4K','2160':'4K','1080':'1080p','720':'720p','576':'576p','480':'480p','sd':'SD'}[String(r||'').toLowerCase()]||'');
const durationText=ms=>{const m=Math.round((ms||0)/60000);return m>=60?`${Math.floor(m/60)} 小时 ${m%60} 分`:m?`${m} 分钟`:'';};
// On GitHub Pages the catalog lives on the Windows backend; served by that backend itself, paths stay relative.
const hostedPage=location.hostname.endsWith('.github.io');
const store={get:key=>{try{return localStorage.getItem(key)||'';}catch{return '';}},set:(key,value)=>{try{value?localStorage.setItem(key,value):localStorage.removeItem(key);}catch{}}};
// The Windows backend's HTTPS tunnel address; a device only needs to type one if it differs.
const DEFAULT_SERVER='https://windows.tailc0b5f5.ts.net:8443';
const serverBase=()=>hostedPage?store.get('plexus-server')||DEFAULT_SERVER:'';
let session={local:true};
let signedIn;const authReady=new Promise(resolve=>signedIn=resolve);
async function request(path,data){const headers={};const token=store.get('plexus-token');if(token)headers.Authorization='Bearer '+token;if(data!==undefined)headers['Content-Type']='application/json';return fetch(serverBase()+path,data===undefined?(token?{headers}:{}):{method:'POST',headers,body:JSON.stringify(data)});}
async function api(path,data){await authReady;const response=await request(path,data);if(response.status===401){store.set('plexus-token','');showLogin('登录已过期，请重新登录。');throw new Error('登录已过期，请重新登录');}const result=await response.json();if(!response.ok)throw Object.assign(new Error(result.error||'操作未完成'),{netdiskLogin:result.netdisk_login||''});return result;}
function showLogin(message=''){const dialog=$('#login-dialog');$('#login-server-field').hidden=!hostedPage||!(message.startsWith('无法连接')||store.get('plexus-server')||!DEFAULT_SERVER);$('#login-change-server').hidden=!hostedPage||!$('#login-server-field').hidden;$('#login-server').value=serverBase();$('#login-message').textContent=message||'影视库保存在 Windows 电脑上，输入访问密码后即可浏览。这台设备之后会保持登录。';$('#login-message').classList.toggle('error-text',!!message);if(!dialog.open)dialog.showModal();}
$('#login-dialog').addEventListener('cancel',event=>event.preventDefault());
$('#login-change-server').onclick=()=>{$('#login-server-field').hidden=false;$('#login-change-server').hidden=true;$('#login-server').focus();};
$('#login-form').onsubmit=async event=>{event.preventDefault();const button=$('button[type="submit"]',event.target);button.disabled=true;
  try{if(hostedPage){const server=$('#login-server').value.trim().replace(/\/+$/,'');if(!/^https:\/\//.test(server))throw new Error('后台地址需要以 https:// 开头。');store.set('plexus-server',server===DEFAULT_SERVER?'':server);}
    const response=await request('/api/login',{password:$('#login-password').value});const result=await response.json();if(!response.ok)throw new Error(result.error||'登录失败');store.set('plexus-token',result.token);location.reload();}
  catch(error){showLogin(error instanceof TypeError?'无法连接 Plexus 后台，请确认地址正确、Windows 电脑开机且服务正在运行。':error.message);button.disabled=false;}};
let toastTimer;
function toast(message,error=false){const el=$('#toast');el.textContent=message;el.classList.toggle('error',error);el.hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.hidden=true,4200);}
const onError=error=>toast(error.message||String(error),true);
$$('[data-close]').forEach(button=>button.onclick=()=>button.closest('dialog').close());
$$('dialog.sheet').forEach(dialog=>dialog.addEventListener('click',event=>{if(event.target===dialog&&dialog.id!=='login-dialog')dialog.close();}));
// On GitHub Pages, links between pages keep working because they are relative.
async function startPage(ready){
  try{const response=await request('/api/session');session=await response.json();}
  catch{showLogin(hostedPage&&!serverBase()?'':'无法连接 Plexus 后台，请确认 Windows 电脑开机且服务正在运行。');return;}
  if(!session.authenticated){showLogin();return;}
  signedIn();ready();
}

// Posters come from Plex through the backend. A signed-in device sends its token, so the image is fetched rather than linked.
const posterCache=new Map();
async function posterURL(query,width=300){const path=`/api/poster?${query}&w=${width}`;if(!store.get('plexus-token'))return serverBase()+path;
  if(!posterCache.has(path))posterCache.set(path,request(path).then(r=>r.ok?r.blob():Promise.reject()).then(b=>URL.createObjectURL(b)));
  return posterCache.get(path);}
const posterObserver=new IntersectionObserver(entries=>entries.forEach(entry=>{if(!entry.isIntersecting)return;const img=entry.target;posterObserver.unobserve(img);
  const fail=()=>img.closest('.poster').classList.add('no-poster');img.onload=()=>img.classList.add('loaded');img.onerror=fail;
  posterURL(img.dataset.poster,+img.dataset.width||300).then(url=>{img.src=url;}).catch(fail);}),{rootMargin:'400px'});
// item: anything with a title and kind; query: how the backend finds its poster ('id=…' or 'url=…'), or '' for none.
function posterHTML(item,query,width=300){
  return `<div class="poster${query?'':' no-poster'}">${query?`<img alt="" data-poster="${escapeHTML(query)}" data-width="${width}">`:''}<span class="poster-glyph">${icon(item.kind==='show'?'tv':'film')}<span>${escapeHTML(item.title)}</span></span></div>`;
}
const itemPoster=item=>item.poster||item.thumb?'id='+encodeURIComponent(item.id):'';
const observePosters=root=>$$('img[data-poster]',root).forEach(img=>posterObserver.observe(img));
