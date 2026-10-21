'use strict';
// Downloads: paste share links, choose files and a library folder, follow the queue, then confirm Plex names.
const STATES={queued:'排队中',saving:'正在转存到你的网盘',downloading:'下载中',review:'等你确认名称',applying:'正在放入媒体库',done:'已放入媒体库',failed:'失败',canceled:'已取消'};
const FILE_STATES={waiting:'等待转存',ready:'排队中',downloading:'下载中',paused:'已暂停',done:'已下载',failed:'失败',canceled:'已取消'};
const LOGIN_BUSY=['waiting','scanned','checking'];
const dl={batches:[],netdisk:{},locations:[],shares:[],timer:null,signing:'',loginTimer:null,pendingPaste:false,cards:new Map()};
const speedText=n=>n?`${sizeText(n)}/s`:'';
const etaText=(left,speed)=>{if(!speed||left<=0)return '';const s=Math.round(left/speed);return s>=3600?`约 ${Math.floor(s/3600)} 小时 ${Math.round(s%3600/60)} 分`:s>=60?`约 ${Math.round(s/60)} 分钟`:`约 ${s} 秒`;};
const percent=(done,size)=>size?Math.min(100,done/size*100):0;
const relative=(path,base)=>path.toLowerCase().startsWith(base.toLowerCase()+'\\')?path.slice(base.length+1):path;
const qrSVG=rows=>`<svg viewBox="-4 -4 ${rows.length+8} ${rows.length+8}" role="img" aria-label="登录二维码" shape-rendering="crispEdges"><rect x="-4" y="-4" width="100%" height="100%" fill="#fff" stroke="none"/><path fill="#000" stroke="none" d="${rows.map((row,y)=>[...row].map((c,x)=>c==='1'?`M${x} ${y}h1v1h-1z`:'').join('')).join('')}"/></svg>`;

// Drive sign-in ------------------------------------------------------------------------------------------------------
function renderAccounts(){
  $('#accounts').innerHTML=Object.entries(dl.netdisk).map(([key,d])=>`<span class="account"><span class="dot${d.signed_in?' on':''}"></span>${escapeHTML(d.label)}${d.signed_in?`<small>${escapeHTML(d.name||'已登录')}</small><button class="text-button" data-logout="${key}">退出</button>`:`<button class="text-button" data-login="${key}">扫码登录</button>`}</span>`).join('');
}
function renderLogin(drive){
  const login=drive.login||{},shown=['waiting','scanned'].includes(login.state),key=login.link||login.image||'';
  $('#drive-login-title').textContent=`扫码登录${drive.label}`;$('#drive-qr').hidden=!shown;
  if(shown&&$('#drive-qr').dataset.key!==key){$('#drive-qr').innerHTML=login.qr?qrSVG(login.qr):`<img src="${escapeHTML(login.image)}" alt="${escapeHTML(drive.label)}登录二维码">`;$('#drive-qr').dataset.key=key;}
  $('#drive-login-status').textContent={waiting:`用${drive.app}扫一扫这个二维码，然后在手机上确认登录。二维码过期后会自动换新。`,scanned:'已扫码，请在手机上确认登录。',
    checking:`正在登录${drive.label}…`,failed:login.error||'登录没有完成，请重新获取二维码。'}[login.state]||'';
  $('#drive-login-retry').hidden=login.state!=='failed';
}
async function signIn(key){
  try{dl.signing=key;renderLogin(await api('/api/netdisk/login',{drive:key}));
    if(!$('#login-drive-dialog').open)$('#login-drive-dialog').showModal();clearTimeout(dl.loginTimer);dl.loginTimer=setTimeout(pollLogin,2000);
  }catch(error){onError(error);}
}
async function pollLogin(){
  clearTimeout(dl.loginTimer);if(!$('#login-drive-dialog').open)return;
  try{dl.netdisk=await api('/api/netdisk');renderAccounts();const drive=dl.netdisk[dl.signing];if(!drive)return;renderLogin(drive);
    if(drive.signed_in){$('#login-drive-dialog').close();toast(`${drive.label}已登录`);if(dl.pendingPaste){dl.pendingPaste=false;readLinks();}return;}
    if(LOGIN_BUSY.includes(drive.login.state))dl.loginTimer=setTimeout(pollLogin,2000);
  }catch(error){onError(error);}
}
$('#accounts').onclick=async event=>{
  const login=event.target.closest('[data-login]'),logout=event.target.closest('[data-logout]');
  if(login)signIn(login.dataset.login);
  if(logout)try{await api('/api/netdisk/logout',{drive:logout.dataset.logout});dl.netdisk=(await api('/api/netdisk'));renderAccounts();}catch(error){onError(error);}
};
$('#drive-login-retry').onclick=()=>signIn(dl.signing);
$('#login-drive-dialog').addEventListener('close',()=>clearTimeout(dl.loginTimer));

// Reading links and choosing files -----------------------------------------------------------------------------------
function locationOptions(kind){
  // Folders of the matching kind come first; every choice shows how much room is left.
  const sorted=[...dl.locations].sort((a,b)=>(a.kind!==kind)-(b.kind!==kind));
  return '<option value="">选择放到哪个盘、哪个媒体库…</option>'+sorted.map(l=>`<option value="${escapeHTML(l.path)}">${escapeHTML(l.drive)} ${escapeHTML(l.title)} · ${escapeHTML(l.path.split('\\').pop())}（剩余 ${sizeText(l.free)}）</option>`).join('');
}
function shareHTML(share,index){
  if(share.error)return `<div class="share failed"><strong>${escapeHTML(share.label)}</strong><p class="error-text">${escapeHTML(share.error)}</p></div>`;
  const files=share.files.map(f=>`<label class="pick${f.kind?'':' skip'}"><input type="checkbox" data-file="${f.index}" ${f.kind?'checked':'disabled'}><span class="pick-name" title="${escapeHTML(f.path)}">${escapeHTML(f.path)}</span><span class="badge${f.kind==='video'?' res':''}">${f.kind==='video'?'视频':f.kind==='subtitle'?'字幕':'不下载'}</span><span class="muted">${sizeText(f.size)}</span></label>`).join('');
  const g=share.guess||{};
  return `<div class="share" data-share="${index}"><div class="share-head"><div><strong>${escapeHTML(share.title||'分享')}</strong><small>${escapeHTML(share.label)} · ${share.files.length} 个文件${g.title?` · 看起来是${g.kind==='show'?'剧集':'电影'}「${escapeHTML(g.title)}${g.year?` (${g.year})`:''}」`:''}</small></div>
    <label class="check-all"><input type="checkbox" data-all checked>全选</label></div><div class="picks">${files}</div>
    <div class="share-foot"><select data-location>${locationOptions(g.kind)}</select><span class="pick-total muted"></span><button class="button" data-add>${icon('download')}<span>加入下载队列</span></button></div></div>`;
}
function updateTotals(card){const share=dl.shares[+card.dataset.share];const chosen=$$('[data-file]:checked',card).map(i=>share.files[+i.dataset.file]);
  $('.pick-total',card).textContent=`已选 ${chosen.length} 个 · ${sizeText(chosen.reduce((n,f)=>n+f.size,0))}`;}
async function readLinks(){
  const text=$('#paste').value.trim(),button=$('#paste-form button');if(!text)return;
  $('#paste-error').hidden=true;button.disabled=true;$('#shares').innerHTML='<p class="muted">正在读取分享链接…</p>';
  try{const result=await api('/api/downloads/resolve',{text});dl.shares=result.shares;dl.locations=result.locations;
    $('#shares').innerHTML=dl.shares.map(shareHTML).join('');$$('#shares .share[data-share]').forEach(updateTotals);
  }catch(error){$('#shares').innerHTML='';if(error.netdiskLogin){dl.pendingPaste=true;signIn(error.netdiskLogin);}else{$('#paste-error').textContent=error.message;$('#paste-error').hidden=false;}}
  finally{button.disabled=false;}
}
$('#paste-form').onsubmit=event=>{event.preventDefault();readLinks();};
$('#shares').onchange=event=>{const card=event.target.closest('.share[data-share]');if(!card)return;
  if(event.target.matches('[data-all]'))$$('[data-file]:not(:disabled)',card).forEach(i=>i.checked=event.target.checked);updateTotals(card);};
$('#shares').onclick=async event=>{const add=event.target.closest('[data-add]');if(!add)return;const card=add.closest('.share');const share=dl.shares[+card.dataset.share];
  const location=$('[data-location]',card).value;if(!location){toast('请先选择放到哪个盘、哪个媒体库。',true);$('[data-location]',card).focus();return;}
  add.disabled=true;
  try{await api('/api/downloads/add',{token:share.token,files:$$('[data-file]:checked',card).map(i=>+i.dataset.file),location});
    card.remove();if(!$('#shares .share[data-share]'))$('#paste').value='';toast('已加入下载队列。');loadQueue();
  }catch(error){add.disabled=false;onError(error);}};

// The queue ----------------------------------------------------------------------------------------------------------
function fileRow(f,batch){
  const live=f.state==='downloading';const left=f.size-f.done;
  const note=f.error?(f.retry_in?`${f.error}（${f.retry_in} 秒后重试）`:f.error):live?[speedText(f.speed),etaText(left,f.speed)].filter(Boolean).join(' · '):'';
  const tools=batch.state==='downloading'||batch.state==='queued'||batch.state==='saving'?[
    ['ready','downloading'].includes(f.state)&&`<button class="icon-button small" data-file-action="pause" data-id="${f.id}" title="暂停">${icon('pause')}</button>`,
    f.state==='paused'&&`<button class="icon-button small" data-file-action="resume" data-id="${f.id}" title="继续">${icon('play')}</button>`,
    f.state==='failed'&&`<button class="icon-button small" data-file-action="retry" data-id="${f.id}" title="重试">${icon('refresh')}</button>`,
    !['done','canceled'].includes(f.state)&&`<button class="icon-button small danger" data-file-action="cancel" data-id="${f.id}" title="不下载这个文件">${icon('close')}</button>`].filter(Boolean).join(''):
    f.state==='failed'&&batch.state==='review'?`<button class="icon-button small" data-file-action="retry" data-id="${f.id}" title="重试">${icon('refresh')}</button>`:'';
  return `<li class="dl-file ${f.state}" data-file-id="${f.id}"><div class="dl-file-main"><span class="file-name" title="${escapeHTML(f.path)}">${escapeHTML(f.path.split('/').pop())}</span><span class="dl-state">${FILE_STATES[f.state]||f.state}</span></div>
    <div class="bar"><span style="width:${percent(f.done,f.size).toFixed(1)}%"></span></div><div class="dl-file-meta"><span class="dl-bytes">${sizeText(f.done)} / ${sizeText(f.size)}</span><span class="dl-note${f.error?' warn-text':''}">${escapeHTML(note)}</span><span class="dl-tools">${tools}</span></div></li>`;
}
function reviewHTML(b){
  const id=b.identity||{};const candidates=(id.candidates||[]).map((c,i)=>`<button class="candidate${c.guid&&c.guid===id.guid?' on':''}" data-candidate="${i}">${posterHTML(c,c.thumb?'url='+encodeURIComponent(c.thumb):'')}<span><strong>${escapeHTML(c.title)}</strong><small>${[c.year,c.kind==='show'?'剧集':'电影'].filter(Boolean).join(' · ')}</small></span></button>`).join('');
  const rows=b.plan.map(p=>{const video=/\.(mkv|mp4|m4v|avi|rmvb|rm|ts|m2ts|mts|mov|wmv|flv|webm|mpg|mpeg|vob|iso)$/i.test(p.path);
    const numbers=id.kind==='show'&&video?`<span class="numbers">S<input data-season="${p.id}" value="${p.season??''}" inputmode="numeric" maxlength="2">E<input data-episode="${p.id}" value="${p.episode??''}" inputmode="numeric" maxlength="4"></span>`:'';
    return `<li><span class="from" title="${escapeHTML(p.path)}">${escapeHTML(p.path.split('/').pop())}</span>${numbers}<span class="arrow">→</span><span class="to" title="${escapeHTML(p.target)}">${escapeHTML(relative(p.target,b.location))}${p.exists?' <span class="badge warn">已有同名，会加编号</span>':''}</span></li>`;}).join('');
  return `<div class="review" data-review="${b.id}"><h3>确认名称</h3><p class="muted">下载完成了。Plexus 在 Plex 的在线资料库里找到下面这些可能的结果，选一个或者自己修改，然后放入「${escapeHTML(b.location)}」。</p>
    ${candidates?`<div class="candidates">${candidates}</div>`:'<p class="muted">没有在 Plex 的在线资料库里找到，名称是从文件名推测的。</p>'}
    <div class="row identity"><label class="grow">片名<input data-field="title" value="${escapeHTML(id.title||'')}"></label><label class="year">年份<input data-field="year" value="${id.year||''}" inputmode="numeric" maxlength="4"></label>
      <label>类型<select data-field="kind"><option value="movie"${id.kind==='movie'?' selected':''}>电影</option><option value="show"${id.kind==='show'?' selected':''}>剧集</option></select></label><button class="button secondary" data-reidentify>${icon('refresh')}<span>更新名称</span></button></div>
    <ul class="renames">${rows}</ul>
    <div class="dialog-actions"><button class="button secondary" data-apply="keep">保留原名放入</button><button class="button" data-apply="rename">${icon('check')}<span>按 Plex 格式重命名并放入媒体库</span></button></div></div>`;
}
function batchHTML(b){
  const left=b.size-b.done;const live=['queued','saving','downloading'].includes(b.state);
  const counts=b.files.reduce((m,f)=>(m[f.state]=(m[f.state]||0)+1,m),{});
  const tools=[live&&b.files.some(f=>['ready','downloading'].includes(f.state))&&`<button class="button secondary small" data-action="pause">${icon('pause')}<span>全部暂停</span></button>`,
    live&&counts.paused&&`<button class="button secondary small" data-action="resume">${icon('play')}<span>全部继续</span></button>`,
    (b.state==='failed'||counts.failed)&&!['done','canceled'].includes(b.state)&&`<button class="button secondary small" data-action="retry">${icon('refresh')}<span>重试失败的</span></button>`,
    !['done','canceled','applying'].includes(b.state)&&`<button class="button secondary small danger" data-action="cancel">${icon('close')}<span>取消</span></button>`,
    ['done','canceled','failed'].includes(b.state)&&`<button class="button secondary small" data-action="remove">${icon('trash')}<span>删除记录</span></button>`].filter(Boolean).join('');
  const done=b.state==='done'?`<p class="muted">已放入：${[...new Set(b.plan.map(p=>relative(p.target,b.location).split('\\')[0]))].map(escapeHTML).join('、')}。Plex 会自动扫描。</p>`:'';
  return `<article class="batch ${b.state}" data-batch="${b.id}"><div class="batch-head"><div class="batch-title"><strong>${escapeHTML(b.identity?.title&&b.state==='done'?b.identity.title:b.title||'下载')}</strong>
    <small>${escapeHTML(b.label)} · ${b.files.length} 个文件 · ${sizeText(b.size)} → ${escapeHTML(b.location)}</small></div><span class="state-badge ${b.state}">${STATES[b.state]||b.state}</span></div>
    ${live||b.state==='review'?`<div class="bar big"><span style="width:${percent(b.done,b.size).toFixed(1)}%"></span></div><p class="batch-progress muted">${sizeText(b.done)} / ${sizeText(b.size)}${b.speed?` · ${speedText(b.speed)} · ${etaText(left,b.speed)}`:''}</p>`:''}
    ${b.error?`<p class="error-text">${escapeHTML(b.error)}</p>`:''}${done}
    ${b.state==='review'?reviewHTML(b):''}
    <details class="dl-files"${live&&b.files.length<=6?' open':''}><summary>文件（${Object.entries(counts).map(([s,n])=>`${FILE_STATES[s]||s} ${n}`).join('，')}）</summary><ul>${b.files.map(f=>fileRow(f,b)).join('')}</ul></details>
    <div class="batch-tools">${tools}</div></article>`;
}
// A card is rebuilt only when what it shows changes shape, so typing in the review form is never interrupted.
const shape=b=>JSON.stringify([b.state,b.error,b.plan,b.identity,b.files.map(f=>[f.state,f.error&&!f.retry_in])]);
function renderQueue(){
  const queue=$('#queue');
  if(!dl.batches.length){queue.innerHTML='<p class="empty">还没有下载。在上面粘贴网盘分享链接开始。</p>';dl.cards.clear();return;}
  $('.empty',queue)?.remove();
  const ids=new Set(dl.batches.map(b=>b.id));
  for(const [id,el] of dl.cards)if(!ids.has(id)){el.remove();dl.cards.delete(id);}
  let previous=null;
  for(const b of dl.batches){
    let el=dl.cards.get(b.id);const sig=shape(b);
    if(!el||el.dataset.sig!==sig&&!el.contains(document.activeElement)){
      const open=el?$('.dl-files',el)?.open:undefined;
      const fresh=document.createRange().createContextualFragment(batchHTML(b)).firstElementChild;fresh.dataset.sig=sig;
      if(open!==undefined&&$('.dl-files',fresh))$('.dl-files',fresh).open=open;
      if(el)el.replaceWith(fresh);el=fresh;dl.cards.set(b.id,el);observePosters(el);
    }else{
      // Only the numbers moved: update bars and speeds in place.
      const bar=$('.bar.big span',el);if(bar)bar.style.width=percent(b.done,b.size).toFixed(1)+'%';
      const progress=$('.batch-progress',el);if(progress)progress.textContent=`${sizeText(b.done)} / ${sizeText(b.size)}${b.speed?` · ${speedText(b.speed)} · ${etaText(b.size-b.done,b.speed)}`:''}`;
      for(const f of b.files){const row=$(`[data-file-id="${f.id}"]`,el);if(!row)continue;
        $('.bar span',row).style.width=percent(f.done,f.size).toFixed(1)+'%';$('.dl-bytes',row).textContent=`${sizeText(f.done)} / ${sizeText(f.size)}`;
        const note=f.error?(f.retry_in?`${f.error}（${f.retry_in} 秒后重试）`:f.error):f.state==='downloading'?[speedText(f.speed),etaText(f.size-f.done,f.speed)].filter(Boolean).join(' · '):'';
        $('.dl-note',row).textContent=note;}
    }
    if(previous?previous.nextElementSibling!==el:queue.firstElementChild!==el)previous?previous.after(el):queue.prepend(el);
    previous=el;
  }
  const active=dl.batches.filter(b=>['queued','saving','downloading'].includes(b.state));const speed=active.reduce((n,b)=>n+b.speed,0);
  const review=dl.batches.filter(b=>b.state==='review').length;
  $('#queue-summary').textContent=[active.length&&`${active.length} 个进行中${speed?` · ${speedText(speed)}`:''}`,review&&`${review} 个等你确认名称`].filter(Boolean).join(' · ');
}
async function loadQueue(){
  clearTimeout(dl.timer);
  try{const data=await api('/api/downloads');dl.batches=data.batches;dl.netdisk=data.netdisk;dl.locations=data.locations;renderAccounts();renderQueue();
    const busy=dl.batches.some(b=>['queued','saving','downloading','applying'].includes(b.state));dl.timer=setTimeout(loadQueue,busy?1500:8000);
  }catch(error){dl.timer=setTimeout(loadQueue,10000);onError(error);}
}
function reviewData(card){
  const data={id:card.dataset.review};for(const input of $$('[data-field]',card))data[input.dataset.field]=input.value;
  data.guid=card.dataset.guid??(dl.batches.find(b=>b.id===data.id)?.identity?.guid||'');
  data.files=$$('[data-season]',card).map(i=>({id:i.dataset.season,season:i.value,episode:$(`[data-episode="${i.dataset.season}"]`,card).value}));
  return data;
}
$('#queue').onclick=async event=>{
  const card=event.target.closest('[data-batch]');if(!card)return;const id=card.dataset.batch;
  const action=event.target.closest('[data-action]'),fileAction=event.target.closest('[data-file-action]');
  try{
    if(action){
      // Cancelling throws away what has downloaded so far, so it takes a second click.
      if(action.dataset.action==='cancel'&&!action.classList.contains('armed')){action.classList.add('armed');$('span',action).textContent='再点一次确认取消';setTimeout(()=>{if(action.isConnected){action.classList.remove('armed');$('span',action).textContent='取消';}},4000);return;}
      await api('/api/downloads/control',{batch:id,action:action.dataset.action});loadQueue();return;}
    if(fileAction){await api('/api/downloads/control',{file:fileAction.dataset.id,action:fileAction.dataset.fileAction});loadQueue();return;}
    const review=event.target.closest('[data-review]');if(!review)return;
    const candidate=event.target.closest('[data-candidate]');
    if(candidate){const c=dl.batches.find(b=>b.id===id).identity.candidates[+candidate.dataset.candidate];
      $('[data-field="title"]',review).value=c.title;$('[data-field="year"]',review).value=c.year||'';$('[data-field="kind"]',review).value=c.kind;review.dataset.guid=c.guid;
      $$('.candidate',review).forEach(b=>b.classList.toggle('on',b===candidate));}
    if(candidate||event.target.closest('[data-reidentify]')){const updated=await api('/api/downloads/identify',reviewData(review));
      dl.batches=dl.batches.map(b=>b.id===id?updated:b);document.activeElement?.blur();renderQueue();return;}
    const apply=event.target.closest('[data-apply]');
    if(apply){apply.disabled=true;
      // Names typed but not yet applied are taken into account first.
      if(apply.dataset.apply==='rename')await api('/api/downloads/identify',reviewData(review));
      await api('/api/downloads/apply',{id,rename:apply.dataset.apply==='rename'});toast('已放入媒体库，Plex 会自动扫描。');loadQueue();}
  }catch(error){onError(error);loadQueue();}
};
$('#queue').addEventListener('keydown',event=>{if(event.key==='Enter'&&event.target.matches('.review input')){event.preventDefault();event.target.closest('.review').querySelector('[data-reidentify]').click();}});

startPage(()=>{
  // Links sent over from a wishlist entry on this device.
  let handed='';try{handed=sessionStorage.getItem('plexus-paste')||'';sessionStorage.removeItem('plexus-paste');}catch{}
  if(handed){$('#paste').value=handed;readLinks();}
  loadQueue();
});
