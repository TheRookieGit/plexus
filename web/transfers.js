'use strict';
// Transfers: paste share links, choose files, save them into your own drive. Downloading them is done in the drive's own client.
const STATES={queued:'排队中',saving:'正在转存',done:'已转存',failed:'失败'};
const FILE_STATES={waiting:'等待转存',saved:'已转存',failed:'失败'};
const LOGIN_BUSY=['waiting','scanned','checking'];
const dl={transfers:[],netdisk:{},shares:[],timer:null,signing:'',loginTimer:null,pendingPaste:false,shown:''};
const qrSVG=rows=>`<svg viewBox="-4 -4 ${rows.length+8} ${rows.length+8}" role="img" aria-label="登录二维码" shape-rendering="crispEdges"><rect x="-4" y="-4" width="100%" height="100%" fill="#fff" stroke="none"/><path fill="#000" stroke="none" d="${rows.map((row,y)=>[...row].map((c,x)=>c==='1'?`M${x} ${y}h1v1h-1z`:'').join('')).join('')}"/></svg>`;

// Drive sign-in ------------------------------------------------------------------------------------------------------
function renderAccounts(){
  $('#accounts').innerHTML=Object.entries(dl.netdisk).map(([key,d])=>`<span class="account"><span class="dot${d.signed_in?' on':''}"></span>${escapeHTML(d.label)}${d.signed_in?`<small>${escapeHTML(d.name||'已登录')}</small><button class="text-button" data-logout="${key}" title="登录在 Plexus 和书屿之间共用">退出</button>`:`<button class="text-button" data-login="${key}">扫码登录</button>`}</span>`).join('');
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
  if(logout){
    // The sign-in is shared, so signing out here signs out of 书屿 and the wish hub as well: a second click confirms.
    if(!logout.classList.contains('armed')){const label=dl.netdisk[logout.dataset.logout]?.label||'网盘';logout.classList.add('armed');logout.textContent=`会同时退出书屿和心愿中枢里的${label}，再点一次确认`;
      setTimeout(()=>{if(logout.isConnected){logout.classList.remove('armed');logout.textContent='退出';}},4000);return;}
    try{await api('/api/netdisk/logout',{drive:logout.dataset.logout});dl.netdisk=(await api('/api/netdisk'));renderAccounts();}catch(error){onError(error);}}
};
$('#drive-login-retry').onclick=()=>signIn(dl.signing);
$('#login-drive-dialog').addEventListener('close',()=>clearTimeout(dl.loginTimer));

// Reading links and choosing files -----------------------------------------------------------------------------------
function shareHTML(share,index){
  if(share.error)return `<div class="share failed"><strong>${escapeHTML(share.label)}</strong><p class="error-text">${escapeHTML(share.error)}</p></div>`;
  const files=share.files.map(f=>`<label class="pick${f.kind?'':' skip'}"><input type="checkbox" data-file="${f.index}" ${f.kind?'checked':'disabled'}><span class="pick-name" title="${escapeHTML(f.path)}">${escapeHTML(f.path)}</span><span class="badge${f.kind==='video'?' res':''}">${f.kind==='video'?'视频':f.kind==='subtitle'?'字幕':'不转存'}</span><span class="muted">${sizeText(f.size)}</span></label>`).join('');
  return `<div class="share" data-share="${index}"><div class="share-head"><div><strong>${escapeHTML(share.title||'分享')}</strong><small>${escapeHTML(share.label)} · ${share.files.length} 个文件</small></div>
    <label class="check-all"><input type="checkbox" data-all checked>全选</label></div><div class="picks">${files}</div>
    <label class="share-folder">转存到「Plexus转存」里的文件夹<input data-folder maxlength="100" value="${escapeHTML(share.folder||'')}"></label><div class="share-foot"><span class="pick-total muted"></span><button class="button" data-add>${icon('link')}<span>转存到我的网盘</span></button></div></div>`;
}
function updateTotals(card){const share=dl.shares[+card.dataset.share];const chosen=$$('[data-file]:checked',card).map(i=>share.files[+i.dataset.file]);
  $('.pick-total',card).textContent=`已选 ${chosen.length} 个 · ${sizeText(chosen.reduce((n,f)=>n+f.size,0))}`;}
async function readLinks(){
  const text=$('#paste').value.trim(),button=$('#paste-form button');if(!text)return;
  $('#paste-error').hidden=true;button.disabled=true;$('#shares').innerHTML='<p class="muted">正在读取分享链接…</p>';
  try{const result=await api('/api/transfers/resolve',{text});dl.shares=result.shares;
    $('#shares').innerHTML=dl.shares.map(shareHTML).join('');$$('#shares .share[data-share]').forEach(updateTotals);
  }catch(error){$('#shares').innerHTML='';if(error.netdiskLogin){dl.pendingPaste=true;signIn(error.netdiskLogin);}else{$('#paste-error').textContent=error.message;$('#paste-error').hidden=false;}}
  finally{button.disabled=false;}
}
$('#paste-form').onsubmit=event=>{event.preventDefault();readLinks();};
$('#shares').onchange=event=>{const card=event.target.closest('.share[data-share]');if(!card)return;
  if(event.target.matches('[data-all]'))$$('[data-file]:not(:disabled)',card).forEach(i=>i.checked=event.target.checked);updateTotals(card);};
$('#shares').onclick=async event=>{const add=event.target.closest('[data-add]');if(!add)return;const card=add.closest('.share');const share=dl.shares[+card.dataset.share];
  add.disabled=true;
  try{await api('/api/transfers/add',{token:share.token,files:$$('[data-file]:checked',card).map(i=>+i.dataset.file),folder:$('[data-folder]',card).value});
    card.remove();if(!$('#shares .share[data-share]'))$('#paste').value='';loadQueue();
  }catch(error){add.disabled=false;onError(error);}};

// The record of what was saved --------------------------------------------------------------------------------------
function fileRow(f){
  return `<li class="dl-file ${f.state}"><div class="dl-file-main"><span class="file-name" title="${escapeHTML(f.path)}">${escapeHTML(f.path.split('/').pop())}</span><span class="dl-state">${FILE_STATES[f.state]||f.state}</span></div>
    <div class="dl-file-meta"><span>${sizeText(f.size)}</span><span class="dl-note warn-text">${escapeHTML(f.error)}</span></div></li>`;
}
function transferHTML(t){
  const counts=t.files.reduce((m,f)=>(m[f.state]=(m[f.state]||0)+1,m),{});
  const tools=[t.state==='failed'&&`<button class="button secondary small" data-action="retry">${icon('refresh')}<span>重试失败的</span></button>`,
    t.login&&`<button class="button secondary small" data-login="${t.login}">${icon('link')}<span>扫码登录${escapeHTML(t.label)}</span></button>`,
    t.state!=='saving'&&`<button class="button secondary small" data-action="remove">${icon(t.state==='queued'?'close':'trash')}<span>${t.state==='queued'?'取消':'删除记录'}</span></button>`].filter(Boolean).join('');
  const done=t.state==='done'?`<p class="muted">在${escapeHTML(t.label)}的「${escapeHTML(t.folder)}」文件夹里，到客户端手动下载。删除记录不会删除网盘里的文件。</p>`:'';
  return `<article class="transfer ${t.state}" data-transfer="${t.id}"><div class="transfer-head"><div class="transfer-title"><strong>${escapeHTML(t.title||'分享')}</strong>
    <small>${escapeHTML(t.label)} · ${t.files.length} 个文件 · ${sizeText(t.size)} → ${escapeHTML(t.folder)}</small></div><span class="state-badge ${t.state}">${t.login?`等待登录${escapeHTML(t.label)}`:STATES[t.state]||t.state}</span></div>
    ${t.error?`<p class="error-text">${escapeHTML(t.error)}</p>`:''}${done}
    <details class="dl-files"><summary>文件（${Object.entries(counts).map(([s,n])=>`${FILE_STATES[s]||s} ${n}`).join('，')}）</summary><ul>${t.files.map(fileRow).join('')}</ul></details>
    <div class="transfer-tools">${tools}</div></article>`;
}
function renderQueue(){
  // Rebuilt only when something changed, so a polled refresh does not close an open file list.
  const shown=JSON.stringify(dl.transfers);if(shown===dl.shown)return;dl.shown=shown;
  const queue=$('#queue'),open=new Set($$('.dl-files[open]',queue).map(d=>d.closest('[data-transfer]').dataset.transfer));
  queue.innerHTML=dl.transfers.length?dl.transfers.map(transferHTML).join(''):'<p class="empty">还没有转存。在上面粘贴网盘分享链接开始。</p>';
  $$('[data-transfer]',queue).forEach(card=>{if(open.has(card.dataset.transfer))$('.dl-files',card).open=true;});
  const active=dl.transfers.filter(t=>['queued','saving'].includes(t.state)).length;
  $('#queue-summary').textContent=active?`${active} 个进行中`:'';
}
async function loadQueue(){
  clearTimeout(dl.timer);
  try{const data=await api('/api/transfers');dl.transfers=data.transfers;dl.netdisk=data.netdisk;renderAccounts();renderQueue();
    const busy=dl.transfers.some(t=>['queued','saving'].includes(t.state));dl.timer=setTimeout(loadQueue,busy?1500:8000);
  }catch(error){dl.timer=setTimeout(loadQueue,10000);onError(error);}
}
$('#queue').onclick=async event=>{
  const card=event.target.closest('[data-transfer]');if(!card)return;
  const action=event.target.closest('[data-action]'),login=event.target.closest('[data-login]');
  if(login)return signIn(login.dataset.login);
  if(!action)return;
  try{await api('/api/transfers/control',{id:card.dataset.transfer,action:action.dataset.action});}catch(error){onError(error);}
  loadQueue();
};

// Links sent over from a wishlist entry on this device, or from the wish hub's 「送到 Plexus」 as #links=
// (after the #, so they are never sent to a server, and taken out of the address at once).
function takeHandedLinks(){
  let handed='';try{handed=sessionStorage.getItem('plexus-paste')||'';sessionStorage.removeItem('plexus-paste');}catch{}
  const match=location.hash.match(/^#links=([\s\S]*)$/);
  if(match){history.replaceState(null,'',location.pathname+location.search);try{handed=decodeURIComponent(match[1]);}catch{}}
  if(handed.trim()){$('#paste').value=handed.trim();readLinks();}
}
window.addEventListener('hashchange',takeHandedLinks);
startPage(()=>{
  takeHandedLinks();
  loadQueue();
});
