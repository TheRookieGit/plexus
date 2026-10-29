'use strict';
const state={view:'library',section:'',issue:'',favorite:false,q:'',drive:'',sort:'added',page:1,request:0,status:null,wishes:[]};
const ISSUES={unindexed:'Plex 未收录',missing:'文件丢失',duplicate:'重复与多版本'};

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
  if(s.duplicates)alerts.push(`<button data-issue="duplicate">${number(s.duplicates)} 部影视有重复或多个版本${s.spare?`，可释放约 ${sizeText(s.spare)}`:''}</button>`);
  $('#alert').hidden=!alerts.length||state.view!=='library';$('#alert').innerHTML=alerts.join('');
  const active=t=>state.view==='library'&&!state.issue&&!state.favorite&&state.section===t.id;
  const tabs=[{id:'',title:'全部',count:status.sections.reduce((n,x)=>n+x.count,0)},...status.sections];
  $('#sections').innerHTML=tabs.map(t=>`<button role="tab" data-section="${escapeHTML(t.id)}" aria-selected="${active(t)}">${icon(t.kind==='show'?'tv':t.kind?'film':'folder')}<span>${escapeHTML(t.title)}</span><small>${number(t.count)}</small></button>`).join('')+
    `<button role="tab" data-favorite aria-selected="${state.view==='library'&&state.favorite}">${icon('star')}<span>收藏</span><small>${number(s.favorites)}</small></button>`+
    `<button role="tab" data-wishlist aria-selected="${state.view==='wishlist'}">${icon('heart')}<span>想看</span><small>${number(state.wishes.length)}</small></button>`+
    (state.issue&&state.view==='library'?`<button role="tab" aria-selected="true" data-clear-issue>${escapeHTML(ISSUES[state.issue])}<small>×</small></button>`:'');
  const drive=$('#drive-filter');const chosen=drive.value;
  drive.innerHTML='<option value="">全部硬盘</option>'+s.drives.map(d=>`<option value="${escapeHTML(d.drive)}">${escapeHTML(d.drive)} 盘</option>`).join('');drive.value=chosen;
  const pending=(status.downloads?.active||0)+(status.downloads?.review||0);$('#downloads-count').hidden=!pending;$('#downloads-count').textContent=pending;
  $('#downloads-link').title=status.downloads?.review?`${status.downloads.review} 个下载等你确认名称`:'从网盘链接下载，查看下载进度';
  $('.filter-controls').hidden=state.view!=='library';$('#wish-add').hidden=state.view!=='wishlist';
}
let statusTimer;
async function loadStatus(){clearTimeout(statusTimer);
  try{const status=await api('/api/status');const finished=state.status?.sync.running&&!status.sync.running;renderStatus(status);if(finished)refresh();statusTimer=setTimeout(loadStatus,status.sync.running?1500:30000);}
  catch(error){statusTimer=setTimeout(loadStatus,30000);throw error;}}
function cardHTML(item){
  const res=resolutionText(item.resolution);
  const meta=item.kind==='show'?(item.episodes?`${item.seasons} 季 · ${number(item.episodes)} 集`:`${number(item.files)} 个文件`):(item.files>1?`${item.files} 个文件`:'');
  const flags=[res&&`<span class="badge res">${res}</span>`,...item.drives.split(',').filter(Boolean).map(d=>`<span class="badge">${escapeHTML(d)}</span>`),
    item.source==='disk'?'<span class="badge warn">未收录</span>':item.unindexed?`<span class="badge warn">${item.unindexed} 未收录</span>`:'',item.missing?`<span class="badge danger">${item.missing} 丢失</span>`:'',
    item.versions||item.twins?'<span class="badge dup">重复</span>':''].filter(Boolean).join('');
  return `<button class="card" data-id="${escapeHTML(item.id)}">${posterHTML(item,itemPoster(item))}<span class="flags">${flags}</span>${item.favorite?`<span class="fav-mark" title="已收藏">${icon('star')}</span>`:''}<span class="card-title">${escapeHTML(item.title)}</span><span class="card-meta">${[item.year,meta,sizeText(item.size)].filter(Boolean).map(escapeHTML).join(' · ')}</span></button>`;
}
async function loadItems(append=false){
  const id=++state.request;if(!append)state.page=1;
  const params=new URLSearchParams({sort:state.sort,page:state.page});for(const key of ['section','issue','q','drive'])if(state[key])params.set(key,state[key]);if(state.favorite)params.set('favorite',1);
  try{const data=await api('/api/items?'+params);if(id!==state.request)return;
    const html=data.items.map(cardHTML).join('');
    const empty=state.status?.sync.running&&!state.status?.sync.last_sync?'正在和 Plex 同步，稍等片刻…':state.favorite&&!state.q?'还没有收藏。在影视详情里点「收藏」就会出现在这里。':'没有符合条件的影视。';
    if(append)$('#grid').insertAdjacentHTML('beforeend',html);else $('#grid').innerHTML=html||`<p class="empty">${empty}</p>`;
    observePosters($('#grid'));
    $('#result-count').textContent=data.total?`共 ${number(data.total)} 部`:'';$('#pager').hidden=data.page>=data.pages;
  }catch(error){onError(error);}
}
const refresh=()=>state.view==='wishlist'?loadWishes():loadItems();
function show(changes){Object.assign(state,changes);if(state.status)renderStatus(state.status);refresh();}
$('#sections').onclick=event=>{const tab=event.target.closest('button');if(!tab)return;
  if('section' in tab.dataset)show({view:'library',section:tab.dataset.section,issue:'',favorite:false});
  else if('favorite' in tab.dataset)show({view:'library',section:'',issue:'',favorite:true});
  else if('wishlist' in tab.dataset)show({view:'wishlist',issue:''});
  else if('clearIssue' in tab.dataset)show({issue:''});};
$('#alert').onclick=event=>{const button=event.target.closest('[data-issue]');if(button)show({view:'library',issue:button.dataset.issue,section:'',favorite:false});};
let searchTimer;$('#search').oninput=event=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{state.q=event.target.value.trim();refresh();},250);};
$('#drive-filter').onchange=event=>{state.drive=event.target.value;loadItems();};
$('#sort').onchange=event=>{state.sort=event.target.value;loadItems();};
$('#more').onclick=()=>{state.page++;loadItems(true);};
$('#grid').onclick=event=>{const card=event.target.closest('[data-id]');if(card)openDetail(card.dataset.id);const wish=event.target.closest('[data-wish]');if(wish)openWish(wish.dataset.wish);};
$('#sync-button').onclick=async()=>{try{await api('/api/sync',{});loadStatus();}catch(error){onError(error);}};

// Detail: what Plex knows about the title, and every file behind it.
const fileName=path=>path.split('\\').pop();
const folderName=path=>path.slice(0,path.lastIndexOf('\\'));
let current=null;
function stateTag(row){return row.state==='missing'?'<span class="badge danger">文件丢失</span>':row.state==='offline'?'<span class="badge">硬盘未连接</span>':row.plex?'':'<span class="badge warn">Plex 未收录</span>';}
function copiesOf(item){
  // Which files are extra copies of one movie or episode; parts of one version (CD1, CD2) share a version.
  const groups=new Map();for(const m of item.media){if(m.state!=='ok'||(!m.episode_id&&item.kind!=='movie'))continue;const key=m.episode_id||'movie';if(!groups.has(key))groups.set(key,new Set());groups.get(key).add(m.version);}
  return new Set(item.media.filter(m=>m.state==='ok'&&groups.get(m.episode_id||'movie')?.size>1).map(m=>m.path));
}
function fileRowHTML(row,copies,label=''){
  const tech=[resolutionText(row.resolution),row.video_codec&&row.video_codec.toUpperCase(),row.audio_codec&&row.audio_codec.toUpperCase(),durationText(row.duration)].filter(Boolean).join(' · ');
  const tools=row.state==='ok'?`${session.local?`<button class="icon-button small" data-reveal="${escapeHTML(row.path)}" title="在资源管理器中显示">${icon('locate')}</button>`:''}<button class="icon-button small danger" data-delete-file="${escapeHTML(row.path)}" title="删除这个文件">${icon('trash')}</button>`:'';
  return `<li class="file-row${row.state!=='ok'?' dim':''}"><div class="file-main">${label?`<span class="episode-label">${label}</span>`:''}<span class="file-name" title="${escapeHTML(row.path)}">${escapeHTML(fileName(row.path))}</span>${stateTag(row)}${copies.has(row.path)?'<span class="badge dup">重复</span>':''}</div><div class="file-meta"><span class="badge">${escapeHTML(row.drive)}</span>${tech?`<span>${escapeHTML(tech)}</span>`:''}<span>${sizeText(row.size)}</span>${tools}</div></li>`;
}
function seasonsOf(item){const seasons=new Map();for(const m of item.media){const key=m.episode_id?m.season??0:'extra';if(!seasons.has(key))seasons.set(key,[]);seasons.get(key).push(m);}return seasons;}
const episodeLabel=r=>`E${String(r.episode??'?').padStart(2,'0')}${r.episode_title&&!/^(Episode|第)\s*\d+/.test(r.episode_title)?` ${r.episode_title}`:''}`;
function filesHTML(item){
  const copies=copiesOf(item);
  if(item.kind!=='show'||!item.media.some(m=>m.episode_id)){
    const folders=[...new Set(item.media.map(m=>folderName(m.path)))];
    return `<h3>文件</h3><p class="path">${folders.map(escapeHTML).join('<br>')}</p><ul class="file-list">${item.media.map(m=>fileRowHTML(m,copies)).join('')}</ul>`;
  }
  const seasons=seasonsOf(item);
  return `<h3>分季</h3>`+[...seasons].map(([season,rows],index)=>{
    const ok=rows.filter(r=>r.state==='ok');const episodes=new Set(rows.filter(r=>r.episode_id).map(r=>r.episode_id)).size;
    const title=season==='extra'?'Plex 未收录的文件':season===0?'特别篇':`第 ${season} 季`;
    const dup=rows.filter(r=>copies.has(r.path)).length;
    const head=`<span>${title}</span><small>${episodes?`${episodes} 集 · `:''}${sizeText(ok.reduce((n,r)=>n+r.size,0))}${dup?` · <span class="warn-text">${dup} 个重复文件</span>`:''}</small>`;
    const list=rows.map(r=>fileRowHTML(r,copies,r.episode_id?escapeHTML(episodeLabel(r)):'')).join('');
    return `<details class="season"${index===0&&seasons.size<4||season==='extra'||dup?' open':''}><summary>${head}</summary><p class="path">${[...new Set(rows.map(r=>folderName(r.path)))].map(escapeHTML).join('<br>')}</p><ul class="file-list">${list}</ul></details>`;
  }).join('');
}
async function openDetail(id){
  try{const item=await api('/api/item?'+new URLSearchParams({id}));current=item;
    const facts=[item.section_title,item.year,item.rating&&`评分 ${Number(item.rating).toFixed(1)}`,item.kind==='show'&&item.episodes&&`${item.seasons} 季 ${item.episodes} 集`,sizeText(item.size)].filter(Boolean);
    const subs=item.subtitles.length?`<h3>字幕</h3><ul class="file-list compact">${item.subtitles.map(s=>`<li class="file-row"><div class="file-main"><span class="file-name" title="${escapeHTML(s.path)}">${escapeHTML(fileName(s.path))}</span></div><div class="file-meta"><span>${sizeText(s.size)}</span></div></li>`).join('')}</ul>`:'';
    const notices=[item.source==='disk'&&'Plex 没有收录这个条目，名称是从文件夹名推测的。',item.versions&&`有 ${item.versions} 个重复的版本${item.spare?`，只留一份可释放约 ${sizeText(item.spare)}`:''}。下面标「重复」的文件可以单独删除。`].filter(Boolean);
    const twins=item.twin_items.length?`<h3>同一部影视的其他条目</h3><div class="twins">${item.twin_items.map(t=>`<button class="twin" data-open="${escapeHTML(t.id)}">${escapeHTML(t.title)}${t.year?` (${t.year})`:''}<small>${escapeHTML(state.status?.sections.find(s=>s.id===t.section)?.title||'')} · ${escapeHTML(t.drives)} · ${sizeText(t.size)}</small></button>`).join('')}</div>`:'';
    const deletable=item.media.some(m=>m.state==='ok');
    $('#detail').innerHTML=`<div class="detail-head">${posterHTML(item,itemPoster(item),600)}<div class="detail-info"><h2>${escapeHTML(item.title)}</h2>${item.original_title&&item.original_title!==item.title?`<p class="original">${escapeHTML(item.original_title)}</p>`:''}
      <p class="facts">${facts.map(escapeHTML).join(' · ')}</p>${notices.map(n=>`<p class="notice">${escapeHTML(n)}</p>`).join('')}${item.summary?`<p class="summary-text">${escapeHTML(item.summary)}</p>`:''}
      <div class="actions">${item.plex_url?`<a class="button" href="${escapeHTML(item.plex_url)}" target="_blank" rel="noopener">${icon('open')}<span>在 Plex 中打开</span></a>`:''}
      <button class="button secondary${item.favorite?' on':''}" data-favorite-toggle>${icon('star')}<span>${item.favorite?'已收藏':'收藏'}</span></button>
      ${deletable?`<button class="button secondary danger" data-delete-item>${icon('trash')}<span>删除…</span></button>`:''}</div></div></div>${twins}${filesHTML(item)}${subs}`;
    observePosters($('#detail'));
    if(!$('#detail-dialog').open)$('#detail-dialog').showModal();$('#detail-dialog').scrollTop=0;
  }catch(error){onError(error);}
}
$('#detail').onclick=async event=>{
  const reveal=event.target.closest('[data-reveal]');if(reveal){try{await api('/api/reveal',{path:reveal.dataset.reveal});}catch(error){onError(error);}return;}
  const open=event.target.closest('[data-open]');if(open){openDetail(open.dataset.open);return;}
  if(event.target.closest('[data-favorite-toggle]')){try{await api('/api/favorite',{id:current.id,on:!current.favorite});openDetail(current.id);loadStatus();if(state.favorite)loadItems();}catch(error){onError(error);}return;}
  const file=event.target.closest('[data-delete-file]');if(file){previewDelete({id:current.id,scope:'file',path:file.dataset.deleteFile});return;}
  if(event.target.closest('[data-delete-item]'))chooseDelete(current);
};

// Delete: choose what (for a show: all, one season or one episode), see exactly which folders and files go, then confirm twice.
function chooseDelete(item){
  const episodes=item.media.filter(m=>m.episode_id&&m.state==='ok');
  if(item.kind!=='show'||!episodes.length){previewDelete({id:item.id,scope:'item'});return;}
  const seasons=[...new Set(episodes.map(m=>m.season))].sort((a,b)=>a-b);
  const unique=[...new Map(episodes.map(m=>[m.episode_id,m])).values()];
  $('#delete-body').innerHTML=`<h2>删除「${escapeHTML(item.title)}」</h2><p class="muted">选择要删除的范围，下一步会列出所有要删除的文件夹和文件。</p>
    <div class="scope-list"><label class="scope"><input type="radio" name="scope" value="item" checked><span><strong>整部剧</strong><small>${seasons.length} 季 · ${unique.length} 集 · ${sizeText(item.size)}</small></span></label>
    <label class="scope"><input type="radio" name="scope" value="season"><span><strong>某一季</strong><select id="delete-season">${seasons.map(s=>`<option value="${s}">${s===0?'特别篇':`第 ${s} 季`}</option>`).join('')}</select></span></label>
    <label class="scope"><input type="radio" name="scope" value="episode"><span><strong>某一集</strong><select id="delete-episode">${unique.map(m=>`<option value="${escapeHTML(m.episode_id)}">S${String(m.season).padStart(2,'0')} ${escapeHTML(episodeLabel(m))}</option>`).join('')}</select></span></label></div>
    <div class="dialog-actions"><button class="button secondary" data-close-delete>取消</button><button class="button danger-fill" id="delete-next">下一步</button></div>`;
  $$('#delete-body select').forEach(select=>select.onfocus=select.onchange=()=>{select.closest('.scope').querySelector('input').checked=true;});
  $('#delete-next').onclick=()=>{const scope=$('#delete-body input[name="scope"]:checked').value;
    previewDelete({id:item.id,scope,season:scope==='season'?+$('#delete-season').value:undefined,episode_id:scope==='episode'?$('#delete-episode').value:undefined});};
  if(!$('#delete-dialog').open)$('#delete-dialog').showModal();
}
async function previewDelete(request){
  const body=$('#delete-body');
  try{const plan=await api('/api/delete/preview',request);
    const rows=plan.targets.map(t=>`<li class="target"><span class="target-kind ${t.kind}">${t.kind==='folder'?'整个文件夹':'文件'}</span><span class="target-path">${escapeHTML(t.path)}</span><small>${t.kind==='folder'?`${number(t.files)} 个文件（${number(t.videos)} 个视频）· `:''}${sizeText(t.size)}${t.others.length?`<br>其中还有：${t.others.map(escapeHTML).join('、')}${t.files-t.videos>t.others.length?' 等':''}`:''}</small></li>`).join('');
    body.innerHTML=`<h2>确认要删除的内容</h2><p class="muted">「${escapeHTML(plan.title)}」：下面 ${plan.targets.length} 项共 ${number(plan.files)} 个文件、${sizeText(plan.size)}。文件夹里只有要删除的视频时，会连同字幕、海报等一起删除整个文件夹。</p>
      <ul class="targets">${rows}</ul>
      <div class="dialog-actions"><button class="button secondary" data-close-delete>取消</button><button class="button danger-fill" id="delete-continue">继续</button></div>`;
    $('#delete-continue').onclick=()=>confirmDelete(plan);
    if(!$('#delete-dialog').open)$('#delete-dialog').showModal();
  }catch(error){onError(error);}
}
function confirmDelete(plan){
  const body=$('#delete-body');let seconds=3;
  body.innerHTML=`<div class="danger-box">${icon('trash')}<div><h2>永久删除，无法恢复</h2><p>这些文件不会进入回收站，NAS 上的文件也没有回收站。确定要删除「${escapeHTML(plan.title)}」的 ${number(plan.files)} 个文件（${sizeText(plan.size)}）吗？</p></div></div>
    <div class="dialog-actions"><button class="button secondary" data-close-delete>取消</button><button class="button danger-fill" id="delete-final" disabled>永久删除（${seconds}）</button></div>`;
  const button=$('#delete-final');
  const timer=setInterval(()=>{seconds--;if(!button.isConnected){clearInterval(timer);return;}button.textContent=seconds>0?`永久删除（${seconds}）`:'永久删除';if(seconds<=0){button.disabled=false;clearInterval(timer);}},1000);
  button.onclick=async()=>{button.disabled=true;button.textContent='正在删除…';
    try{const result=await api('/api/delete/confirm',{token:plan.token});$('#delete-dialog').close();
      toast(result.failed.length?`已删除 ${result.deleted} 项，${result.failed.length} 项没能删除：${result.failed[0].error}`:`已删除 ${sizeText(result.size)}。Plex 会自动更新。`,!!result.failed.length);
      $('#detail-dialog').close();setTimeout(()=>{loadStatus();loadItems();},1200);
    }catch(error){button.disabled=false;button.textContent='永久删除';onError(error);}};
}
$('#delete-dialog').addEventListener('click',event=>{if(event.target.closest('[data-close-delete]'))$('#delete-dialog').close();});

// Wishlist: titles still to get, looked up in Plex's online catalog, with the share links to download them from.
async function loadWishes(){
  try{const data=await api('/api/wishlist');state.wishes=data.items;state.hub=data.hub;if(state.status)renderStatus(state.status);if(state.view!=='wishlist')return;
    const q=state.q.toLowerCase();const shown=state.wishes.filter(w=>!q||w.title.toLowerCase().includes(q));
    $('#grid').innerHTML=shown.map(w=>`<button class="card" data-wish="${escapeHTML(w.id)}">${posterHTML(w,w.thumb?'id='+encodeURIComponent('wish:'+w.id):'')}<span class="flags">${w.owned?'<span class="badge res">已拥有</span>':''}${w.links?`<span class="badge">${icon('link')}有链接</span>`:''}</span><span class="card-title">${escapeHTML(w.title)}</span><span class="card-meta">${[w.year,w.kind==='show'?'剧集':'电影',w.note].filter(Boolean).map(escapeHTML).join(' · ')}</span></button>`).join('')||`<p class="empty">${q?'没有符合条件的想看。':'还没有想看的影视。点「添加想看」，搜索片名并记下网盘链接。'}</p>`;
    observePosters($('#grid'));$('#result-count').textContent=shown.length?`共 ${number(shown.length)} 部`:'';$('#pager').hidden=true;
  }catch(error){onError(error);}
}
let wishPick={};
function openWish(id){
  const wish=state.wishes.find(w=>w.id===id)||{};wishPick={guid:wish.guid||'',thumb:wish.thumb||'',summary:wish.summary||''};
  $('#wish-title-text').textContent=wish.id?'编辑想看':'添加想看';$('#wish-id').value=wish.id||'';$('#wish-title').value=wish.title||'';$('#wish-year').value=wish.year||'';
  $('#wish-kind').value=wish.kind||'movie';$('#wish-links').value=wish.links||'';$('#wish-note').value=wish.note||'';$('#wish-results').innerHTML='';$('#wish-query').value='';
  wishShots=[...(wish.shots||[])];$('#wish-shots-field').hidden=!state.hub;renderWishShots();
  $('#wish-delete').hidden=!wish.id;$('#wish-download').hidden=!wish.links;$('#wish-owned').hidden=!wish.owned;$('#wish-owned').dataset.open=wish.owned||'';
  $('#wish-dialog').showModal();(wish.id?$('#wish-links'):$('#wish-query')).focus();
}
$('#wish-add').onclick=()=>openWish('');
let wishSearchTimer;
$('#wish-query').oninput=event=>{clearTimeout(wishSearchTimer);const q=event.target.value.trim();if(!q){$('#wish-results').innerHTML='';return;}
  wishSearchTimer=setTimeout(async()=>{$('#wish-results').innerHTML='<li class="muted">正在搜索…</li>';
    try{const items=(await api('/api/search?'+new URLSearchParams({q}))).items;if($('#wish-query').value.trim()!==q)return;
      $('#wish-results').innerHTML=items.map((r,i)=>`<li><button type="button" data-pick="${i}">${posterHTML(r,r.thumb?'url='+encodeURIComponent(r.thumb):'',300)}<span><strong>${escapeHTML(r.title)}</strong><small>${[r.year,r.kind==='show'?'剧集':'电影',r.original_title].filter(Boolean).map(escapeHTML).join(' · ')}</small></span></button></li>`).join('')||'<li class="muted">没有找到，可以直接在下面填写片名。</li>';
      $('#wish-results').items=items;observePosters($('#wish-results'));
    }catch(error){$('#wish-results').innerHTML=`<li class="error-text">${escapeHTML(error.message)}</li>`;}},350);};
$('#wish-results').onclick=event=>{const pick=event.target.closest('[data-pick]');if(!pick)return;const r=$('#wish-results').items[+pick.dataset.pick];
  $('#wish-title').value=r.title;$('#wish-year').value=r.year||'';$('#wish-kind').value=r.kind;wishPick={guid:r.guid,thumb:r.thumb,summary:r.summary};
  $('#wish-results').innerHTML='';$('#wish-query').value='';$('#wish-links').focus();};
$('#wish-form').onsubmit=async event=>{event.preventDefault();
  try{await api('/api/wishlist/save',{id:$('#wish-id').value,title:$('#wish-title').value,year:$('#wish-year').value,kind:$('#wish-kind').value,links:$('#wish-links').value,note:$('#wish-note').value,...wishPick,...(state.hub?{shots:wishShots}:{})});
    $('#wish-dialog').close();toast('已保存到想看。');loadWishes();}catch(error){onError(error);}};
// Original screenshots, shared with the wish hub: shown from the hub, added here go up to it when the wish is saved.
let wishShots=[];const shotCache=new Map();
function renderWishShots(){
  $('#wish-shots').innerHTML=wishShots.map(id=>`<span class="wish-shot"><img data-shot="${escapeHTML(id)}" alt="原截图"><button type="button" class="wish-shot-remove" data-remove-shot="${escapeHTML(id)}" aria-label="删掉这张截图">×</button></span>`).join('')+'<label class="wish-shot-add"><input id="wish-shot-input" type="file" accept="image/*" multiple hidden><span>＋ 添加截图</span></label>';
  $$('#wish-shots [data-shot]').forEach(async img=>{const id=img.dataset.shot;
    try{if(!shotCache.has(id))shotCache.set(id,(await api('/api/wishlist/shot?'+new URLSearchParams({id}))).data);img.src=shotCache.get(id);img.onclick=()=>window.open().document.write(`<img src="${shotCache.get(id)}" style="max-width:100%">`);}
    catch(error){img.closest('.wish-shot').classList.add('missing');img.title=error.message;}});
  $('#wish-shot-input').onchange=event=>{addWishShots([...event.target.files]);event.target.value='';};
}
// The same size the hub keeps: the long side at most 1600px, as JPEG.
async function shotDataURL(file){
  const bitmap=await createImageBitmap(file);const scale=Math.min(1,1600/Math.max(bitmap.width,bitmap.height));
  const canvas=document.createElement('canvas');canvas.width=Math.round(bitmap.width*scale);canvas.height=Math.round(bitmap.height*scale);
  canvas.getContext('2d').drawImage(bitmap,0,0,canvas.width,canvas.height);
  let data=canvas.toDataURL('image/jpeg',.72);if(data.length>900000)data=canvas.toDataURL('image/jpeg',.5);return data;
}
async function addWishShots(files){
  for(const file of files.filter(f=>f.type.startsWith('image/'))){
    try{const data=await shotDataURL(file);const {id}=await api('/api/wishlist/shot',{data});shotCache.set(id,data);wishShots.push(id);}catch(error){onError(error);}
  }
  renderWishShots();
}
$('#wish-shots').addEventListener('click',event=>{const button=event.target.closest('[data-remove-shot]');if(!button)return;
  wishShots=wishShots.filter(id=>id!==button.dataset.removeShot);renderWishShots();});
$('#wish-dialog').addEventListener('paste',event=>{if(!state.hub)return;const files=[...(event.clipboardData?.files||[])].filter(f=>f.type.startsWith('image/'));
  if(files.length){event.preventDefault();addWishShots(files);}});
$('#wish-delete').onclick=async()=>{try{await api('/api/wishlist/delete',{id:$('#wish-id').value});$('#wish-dialog').close();loadWishes();}catch(error){onError(error);}};
$('#wish-owned').onclick=()=>{$('#wish-dialog').close();openDetail($('#wish-owned').dataset.open);};
// The download page picks the links up from this device's session, so they never travel in the address.
$('#wish-download').onclick=()=>{try{sessionStorage.setItem('plexus-paste',$('#wish-links').value);}catch{}location.href='downloads.html';};

// Settings: the Plex address, and the password other devices sign in with.
$('#settings-button').onclick=()=>{const status=state.status;const server=status?.sync.server;
  $('#plex-url').value=status?.plex_url||'';$('#plex-info').textContent=server?`已连接 ${server.name||'Plex Media Server'}，版本 ${server.version.split('-')[0]}。`:status?.sync.error||'尚未连接。';
  $('#password-info').textContent=session.password_set?'已设置访问密码。设置新密码后，所有设备都需要重新登录。':'还没有设置访问密码。设置后，其他设备和网页版才能登录。';
  $('#plex-form').hidden=$('#password-form').hidden=!session.local;$('#remote-only').hidden=session.local;$('#logout').hidden=session.local||!store.get('plexus-token');
  $('#settings-dialog').showModal();renderHub();};
// The wish hub (心愿中枢): connect with a pairing code made in the hub; the wishlist then follows the hub's movies and series.
async function renderHub(){
  try{const hub=await api('/api/hub');
    $('#hub-status').textContent=hub.paired?`已连接 · 想看清单来自心愿中枢（${number(hub.wishes)} 部）${hub.queued?` · ${number(hub.queued)} 项等联网后发送`:''}${hub.error?' · '+hub.error:''}`:'还没有连接。';
    $('#hub-token-row').hidden=$('#hub-connect').hidden=hub.paired;$('#hub-disconnect').hidden=!hub.paired;
  }catch(error){$('#hub-status').textContent=error.message;}
}
$('#hub-form').onsubmit=async event=>{event.preventDefault();
  try{await api('/api/hub/pair',{token:$('#hub-token').value});$('#hub-token').value='';toast('已连接心愿中枢。');renderHub();loadWishes();}catch(error){$('#hub-status').textContent=error.message;}};
$('#hub-disconnect').onclick=async()=>{const button=$('#hub-disconnect');
  if(!button.classList.contains('armed')){button.classList.add('armed');button.textContent='再点一次确认断开';setTimeout(()=>{if(button.isConnected){button.classList.remove('armed');button.textContent='断开连接';}},4000);return;}
  button.classList.remove('armed');button.textContent='断开连接';
  try{await api('/api/hub/unpair',{});toast('已断开心愿中枢。');renderHub();loadWishes();}catch(error){onError(error);}};
// The hub's 「查看」 opens an owned title here as #item=<id>.
function openHashItem(){const match=location.hash.match(/^#item=(.+)$/);if(!match)return;history.replaceState(null,'',location.pathname+location.search);
  try{openDetail(decodeURIComponent(match[1]));}catch{}}
window.addEventListener('hashchange',openHashItem);
$('#plex-form').onsubmit=async event=>{event.preventDefault();try{await api('/api/settings',{plex_url:$('#plex-url').value.trim()});$('#settings-dialog').close();toast('已保存，正在重新同步。');loadStatus();}catch(error){onError(error);}};
$('#password-form').onsubmit=async event=>{event.preventDefault();try{await api('/api/access/password',{password:$('#password').value});$('#password').value='';session.password_set=true;$('#settings-dialog').close();toast('访问密码已设置。');}catch(error){onError(error);}};
$('#logout').onclick=async()=>{try{await api('/api/logout',{});}catch{}store.set('plexus-token','');location.reload();};

startPage(async()=>{
  try{await loadStatus();}catch(error){onError(error);}
  loadItems();loadWishes();openHashItem();
});
