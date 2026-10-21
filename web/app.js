'use strict';
const $=(s,root=document)=>root.querySelector(s);
const $$=(s,root=document)=>[...root.querySelectorAll(s)];
const escapeHTML=(v='')=>String(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icons={folder:'<path d="M3 6a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z"/>',settings:'<path d="m10 3-1 3-3 1-2 3 1 3-1 3 2 3 3 1 1 2h4l1-2 3-1 2-3-1-3 1-3-2-3-3-1-1-3z"/><circle cx="12" cy="12" r="3"/>',refresh:'<path d="M20 10a8 8 0 0 0-14-5L3 8m0-5v5h5M4 14a8 8 0 0 0 14 5l3-3m0 5v-5h-5"/>',search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',film:'<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4"/>',tv:'<rect x="3" y="6" width="18" height="13" rx="2"/><path d="m8 2 4 4 4-4"/>',drive:'<rect x="3" y="13" width="18" height="7" rx="2"/><path d="M5 13 7 5h10l2 8M17 16.5h.01"/>',open:'<path d="M7 17 17 7M7 7h10v10"/>',locate:'<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/>',file:'<path d="M14 2H5v20h14V7zM14 2v6h5"/>'};
const icon=name=>`<svg viewBox="0 0 24 24" aria-hidden="true">${icons[name]||icons.file}</svg>`;
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
const DEFAULT_SERVER='';
const serverBase=()=>hostedPage?store.get('plexus-server')||DEFAULT_SERVER:'';
let session={local:true};
let signedIn;const authReady=new Promise(resolve=>signedIn=resolve);
async function request(path,data){const headers={};const token=store.get('plexus-token');if(token)headers.Authorization='Bearer '+token;if(data!==undefined)headers['Content-Type']='application/json';return fetch(serverBase()+path,data===undefined?(token?{headers}:{}):{method:'POST',headers,body:JSON.stringify(data)});}
async function api(path,data){await authReady;const response=await request(path,data);if(response.status===401){store.set('plexus-token','');showLogin('登录已过期，请重新登录。');throw new Error('登录已过期，请重新登录');}const result=await response.json();if(!response.ok)throw new Error(result.error||'操作未完成');return result;}
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

// Posters come from Plex through the backend. A signed-in device sends its token, so the image is fetched rather than linked.
const posterCache=new Map();
async function posterURL(id,width=300){const key=id+'|'+width;if(!store.get('plexus-token'))return `${serverBase()}/api/poster?id=${encodeURIComponent(id)}&w=${width}`;
  if(!posterCache.has(key))posterCache.set(key,request(`/api/poster?id=${encodeURIComponent(id)}&w=${width}`).then(r=>r.ok?r.blob():Promise.reject()).then(b=>URL.createObjectURL(b)));
  return posterCache.get(key);}
const posterObserver=new IntersectionObserver(entries=>entries.forEach(entry=>{if(!entry.isIntersecting)return;const img=entry.target;posterObserver.unobserve(img);
  img.onload=()=>img.classList.add('loaded');img.onerror=()=>img.closest('.poster').classList.add('no-poster');posterURL(img.dataset.poster,+img.dataset.width||300).then(url=>{img.src=url;}).catch(()=>img.closest('.poster').classList.add('no-poster'));}),{rootMargin:'400px'});
function posterHTML(item,width=300){
  const glyph=icon(item.kind==='show'?'tv':'film');
  return `<div class="poster${item.poster||item.thumb?'':' no-poster'}">${item.poster||item.thumb?`<img alt="" data-poster="${escapeHTML(item.id)}" data-width="${width}">`:''}<span class="poster-glyph">${glyph}<span>${escapeHTML(item.title)}</span></span></div>`;
}
const observePosters=root=>$$('img[data-poster]',root).forEach(img=>posterObserver.observe(img));

const state={section:'',issue:'',q:'',drive:'',sort:'added',page:1,request:0,status:null};
function summaryHTML(status){
  const s=status.stats;const sync=status.sync;
  const drives=s.drives.map(d=>`<span class="stat"><strong>${escapeHTML(d.drive)}</strong>${number(d.files)} 个视频 · ${sizeText(d.size)}</span>`).join('');
  const synced=sync.running?`正在${escapeHTML(sync.phase||'同步')}…`:sync.last_sync?`${formatTime(sync.last_sync)} 已同步`:'尚未同步';
  return `<span class="stat lead"><strong>${number(s.items)}</strong>部影视</span><span class="stat"><strong>${number(s.files)}</strong>个视频文件</span><span class="stat"><strong>${sizeText(s.size)}</strong>总占用</span>${drives}<span class="stat sync${sync.error?' error-text':''}">${sync.error?escapeHTML(sync.error):synced}${sync.server&&!sync.error?` · Plex ${escapeHTML(sync.server.version.split('-')[0])}`:''}</span>`;
}
function renderStatus(status){
  state.status=status;
  $('#summary').innerHTML=summaryHTML(status);
  $('#sync-button').disabled=status.sync.running;$('#sync-label').textContent=status.sync.running?'同步中…':'同步';
  const s=status.stats;const alerts=[];
  if(s.unindexed)alerts.push(`<button data-issue="unindexed">${number(s.unindexed)} 个视频文件 Plex 还没有收录</button>`);
  if(s.missing)alerts.push(`<button data-issue="missing">${number(s.missing)} 个 Plex 里的文件在硬盘上找不到了</button>`);
  $('#alert').hidden=!alerts.length;$('#alert').innerHTML=alerts.join('');
  const tabs=[{id:'',title:'全部',count:status.sections.reduce((n,x)=>n+x.count,0)},...status.sections];
  $('#sections').innerHTML=tabs.map(t=>`<button role="tab" data-section="${escapeHTML(t.id)}" aria-selected="${!state.issue&&state.section===t.id}">${icon(t.kind==='show'?'tv':t.kind?'film':'folder')}<span>${escapeHTML(t.title)}</span><small>${number(t.count)}</small></button>`).join('')+
    (state.issue?`<button role="tab" aria-selected="true" data-issue="${state.issue}">${state.issue==='missing'?'文件丢失':'Plex 未收录'}<small>×</small></button>`:'');
  const drive=$('#drive-filter');const chosen=drive.value;
  drive.innerHTML='<option value="">全部硬盘</option>'+s.drives.map(d=>`<option value="${escapeHTML(d.drive)}">${escapeHTML(d.drive)} 盘</option>`).join('');drive.value=chosen;
}
let statusTimer;
async function loadStatus(){clearTimeout(statusTimer);
  try{const status=await api('/api/status');const finished=state.status?.sync.running&&!status.sync.running;renderStatus(status);if(finished)loadItems();statusTimer=setTimeout(loadStatus,status.sync.running?1500:30000);}
  catch(error){statusTimer=setTimeout(loadStatus,30000);throw error;}}
function cardHTML(item){
  const res=resolutionText(item.resolution);
  const meta=item.kind==='show'?(item.episodes?`${item.seasons} 季 · ${number(item.episodes)} 集`:`${number(item.files)} 个文件`):(item.files>1?`${item.files} 个文件`:'');
  const flags=[res&&`<span class="badge res">${res}</span>`,...item.drives.split(',').filter(Boolean).map(d=>`<span class="badge">${escapeHTML(d)}</span>`),
    item.source==='disk'?'<span class="badge warn">未收录</span>':item.unindexed?`<span class="badge warn">${item.unindexed} 未收录</span>`:'',item.missing?`<span class="badge danger">${item.missing} 丢失</span>`:''].filter(Boolean).join('');
  return `<button class="card" data-id="${escapeHTML(item.id)}">${posterHTML(item)}<span class="flags">${flags}</span><span class="card-title">${escapeHTML(item.title)}</span><span class="card-meta">${[item.year,meta,sizeText(item.size)].filter(Boolean).map(escapeHTML).join(' · ')}</span></button>`;
}
async function loadItems(append=false){
  const id=++state.request;if(!append)state.page=1;
  const params=new URLSearchParams({sort:state.sort,page:state.page});for(const key of ['section','issue','q','drive'])if(state[key])params.set(key,state[key]);
  try{const data=await api('/api/items?'+params);if(id!==state.request)return;
    const html=data.items.map(cardHTML).join('');
    if(append)$('#grid').insertAdjacentHTML('beforeend',html);else $('#grid').innerHTML=html||`<p class="empty">${state.status?.sync.running&&!state.status?.sync.last_sync?'正在和 Plex 同步，稍等片刻…':'没有符合条件的影视。'}</p>`;
    observePosters($('#grid'));
    $('#result-count').textContent=data.total?`共 ${number(data.total)} 部`:'';$('#pager').hidden=data.page>=data.pages;
  }catch(error){onError(error);}
}
$('#sections').onclick=event=>{const tab=event.target.closest('[data-section],[data-issue]');if(!tab)return;
  if('section' in tab.dataset){state.section=tab.dataset.section;state.issue='';}else state.issue='';renderStatus(state.status);loadItems();};
$('#alert').onclick=event=>{const button=event.target.closest('[data-issue]');if(!button)return;state.issue=button.dataset.issue;state.section='';renderStatus(state.status);loadItems();};
let searchTimer;$('#search').oninput=event=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{state.q=event.target.value.trim();loadItems();},250);};
$('#drive-filter').onchange=event=>{state.drive=event.target.value;loadItems();};
$('#sort').onchange=event=>{state.sort=event.target.value;loadItems();};
$('#more').onclick=()=>{state.page++;loadItems(true);};
$('#grid').onclick=event=>{const card=event.target.closest('[data-id]');if(card)openDetail(card.dataset.id);};
$('#sync-button').onclick=async()=>{try{await api('/api/sync',{});loadStatus();}catch(error){onError(error);}};

// Detail: what Plex knows about the title, and every file behind it.
const fileName=path=>path.split('\\').pop();
const folderName=path=>path.slice(0,path.lastIndexOf('\\'));
function stateTag(row){return row.state==='missing'?'<span class="badge danger">文件丢失</span>':row.state==='offline'?'<span class="badge">硬盘未连接</span>':row.plex?'':'<span class="badge warn">Plex 未收录</span>';}
function fileRowHTML(row,label=''){
  const tech=[resolutionText(row.resolution),row.video_codec&&row.video_codec.toUpperCase(),row.audio_codec&&row.audio_codec.toUpperCase(),durationText(row.duration)].filter(Boolean).join(' · ');
  const locate=session.local&&row.state==='ok'?`<button class="icon-button small" data-reveal="${escapeHTML(row.path)}" title="在资源管理器中显示">${icon('locate')}</button>`:'';
  return `<li class="file-row${row.state!=='ok'?' dim':''}"><div class="file-main">${label?`<span class="episode-label">${label}</span>`:''}<span class="file-name" title="${escapeHTML(row.path)}">${escapeHTML(fileName(row.path))}</span>${stateTag(row)}</div><div class="file-meta"><span class="badge">${escapeHTML(row.drive)}</span>${tech?`<span>${escapeHTML(tech)}</span>`:''}<span>${sizeText(row.size)}</span>${locate}</div></li>`;
}
function filesHTML(item){
  if(item.kind!=='show'||!item.media.some(m=>m.episode_id)){
    const folders=[...new Set(item.media.map(m=>folderName(m.path)))];
    return `<h3>文件</h3><p class="path">${folders.map(escapeHTML).join('<br>')}</p><ul class="file-list">${item.media.map(m=>fileRowHTML(m)).join('')}</ul>`;
  }
  const seasons=new Map();for(const m of item.media){const key=m.episode_id?m.season??0:'extra';if(!seasons.has(key))seasons.set(key,[]);seasons.get(key).push(m);}
  return `<h3>分季</h3>`+[...seasons].map(([season,rows],index)=>{
    const ok=rows.filter(r=>r.state==='ok');const episodes=new Set(rows.filter(r=>r.episode_id).map(r=>r.episode_id)).size;
    const title=season==='extra'?'Plex 未收录的文件':season===0?'特别篇':`第 ${season} 季`;
    const head=`<span>${title}</span><small>${episodes?`${episodes} 集 · `:''}${sizeText(ok.reduce((n,r)=>n+r.size,0))}</small>`;
    const list=rows.map(r=>fileRowHTML(r,r.episode_id?`E${String(r.episode??'?').padStart(2,'0')}${r.episode_title&&!/^(Episode|第)\s*\d+/.test(r.episode_title)?` ${escapeHTML(r.episode_title)}`:''}`:'')).join('');
    return `<details class="season"${index===0&&seasons.size<4||season==='extra'?' open':''}><summary>${head}</summary><p class="path">${[...new Set(rows.map(r=>folderName(r.path)))].map(escapeHTML).join('<br>')}</p><ul class="file-list">${list}</ul></details>`;
  }).join('');
}
async function openDetail(id){
  try{const item=await api('/api/item?'+new URLSearchParams({id}));
    const facts=[item.section_title,item.year,item.rating&&`评分 ${Number(item.rating).toFixed(1)}`,item.kind==='show'&&item.episodes&&`${item.seasons} 季 ${item.episodes} 集`,sizeText(item.size)].filter(Boolean);
    const subs=item.subtitles.length?`<h3>字幕</h3><ul class="file-list compact">${item.subtitles.map(s=>`<li class="file-row"><div class="file-main"><span class="file-name" title="${escapeHTML(s.path)}">${escapeHTML(fileName(s.path))}</span></div><div class="file-meta"><span>${sizeText(s.size)}</span></div></li>`).join('')}</ul>`:'';
    $('#detail').innerHTML=`<div class="detail-head">${posterHTML(item,600)}<div class="detail-info"><h2>${escapeHTML(item.title)}</h2>${item.original_title&&item.original_title!==item.title?`<p class="original">${escapeHTML(item.original_title)}</p>`:''}
      <p class="facts">${facts.map(escapeHTML).join(' · ')}</p>${item.source==='disk'?'<p class="notice">Plex 没有收录这个条目，名称是从文件夹名推测的。</p>':''}${item.summary?`<p class="summary-text">${escapeHTML(item.summary)}</p>`:''}
      <div class="actions">${item.plex_url?`<a class="button" href="${escapeHTML(item.plex_url)}" target="_blank" rel="noopener">${icon('open')}<span>在 Plex 中打开</span></a>`:''}</div></div></div>${filesHTML(item)}${subs}`;
    observePosters($('#detail'));
    if(!$('#detail-dialog').open)$('#detail-dialog').showModal();$('#detail-dialog').scrollTop=0;
  }catch(error){onError(error);}
}
$('#detail').onclick=async event=>{const button=event.target.closest('[data-reveal]');if(!button)return;try{await api('/api/reveal',{path:button.dataset.reveal});}catch(error){onError(error);}};

// Settings: the Plex address, and the password other devices sign in with.
$('#settings-button').onclick=()=>{const status=state.status;const server=status?.sync.server;
  $('#plex-url').value=status?.plex_url||'';$('#plex-info').textContent=server?`已连接 ${server.name||'Plex Media Server'}，版本 ${server.version.split('-')[0]}。`:status?.sync.error||'尚未连接。';
  $('#password-info').textContent=session.password_set?'已设置访问密码。设置新密码后，所有设备都需要重新登录。':'还没有设置访问密码。设置后，其他设备和网页版才能登录。';
  $('#plex-form').hidden=$('#password-form').hidden=!session.local;$('#remote-only').hidden=session.local;$('#logout').hidden=session.local||!store.get('plexus-token');
  $('#settings-dialog').showModal();};
$('#plex-form').onsubmit=async event=>{event.preventDefault();try{await api('/api/settings',{plex_url:$('#plex-url').value.trim()});$('#settings-dialog').close();toast('已保存，正在重新同步。');loadStatus();}catch(error){onError(error);}};
$('#password-form').onsubmit=async event=>{event.preventDefault();try{await api('/api/access/password',{password:$('#password').value});$('#password').value='';session.password_set=true;$('#settings-dialog').close();toast('访问密码已设置。');}catch(error){onError(error);}};
$('#logout').onclick=async()=>{try{await api('/api/logout',{});}catch{}store.set('plexus-token','');location.reload();};

(async()=>{
  try{const response=await request('/api/session');session=await response.json();}
  catch{showLogin(hostedPage&&!serverBase()?'':'无法连接 Plexus 后台，请确认 Windows 电脑开机且服务正在运行。');return;}
  if(!session.authenticated){showLogin();return;}
  signedIn();
  try{await loadStatus();}catch(error){onError(error);}
  loadItems();
})();
