'use strict';
// Folder tree: how many videos each drive and folder holds and how many Plex knows, loaded one level at a time.
function treeCountHTML(c){
  return `<strong>${number(c.total)}</strong> 个视频${c.unindexed?`<small class="warn-text">${number(c.unindexed)} 未收录</small>`:''}<small>${sizeText(c.size)}</small>`;
}
function treeLevelHTML(data,depth){
  const largest=Math.max(1,...data.children.map(c=>c.size));
  const folders=data.children.map(child=>{
    const path=JSON.stringify([...data.path,child.name]);
    return `<li class="tree-node" role="treeitem" aria-expanded="false" style="--depth:${depth}"><button class="tree-row" type="button" data-tree-path="${escapeHTML(path)}" ${child.has_children?'':'disabled'}><span class="tree-caret" aria-hidden="true">${child.has_children?'▸':''}</span><span class="tree-icon">${icon(depth?'folder':'drive')}</span><span class="tree-name" title="${escapeHTML(child.name)}">${escapeHTML(child.name)}${child.label?`<small>${escapeHTML(child.label)}</small>`:''}</span><span class="tree-count">${treeCountHTML(child)}</span><span class="tree-bar" style="--share:${(child.size/largest*100).toFixed(1)}%"></span></button><ul class="tree-children" role="group" hidden></ul></li>`;
  }).join('');
  const files=data.files.map(file=>`<li class="tree-file" role="treeitem" style="--depth:${depth}"><button type="button" data-tree-item="${escapeHTML(file.item_id)}" title="${escapeHTML(file.name)}" ${file.item_id?'':'disabled'}><span class="tree-caret"></span><span class="tree-icon">${icon('file')}</span><span class="tree-name">${escapeHTML(file.name)}</span>${file.plex?'':'<span class="badge warn">未收录</span>'}<span class="tree-size">${sizeText(file.size)}</span></button></li>`).join('');
  const more=data.direct_total>data.files.length?`<li class="tree-more" style="--depth:${depth}">这一层还有 ${number(data.direct_total-data.files.length)} 个文件没有列出，可以用搜索查找。</li>`:'';
  return folders+files+more||`<li class="tree-more" style="--depth:${depth}">这里没有视频。</li>`;
}
async function treeLevel(path){return api('/api/tree?'+new URLSearchParams({path:JSON.stringify(path)}));}
async function openTree(){
  $('#tree-root').innerHTML='<li class="tree-more">正在统计…</li>';$('#tree-summary').textContent='';
  if(!$('#tree-dialog').open)$('#tree-dialog').showModal();
  try{const data=await treeLevel([]);const c=data.counts;
    $('#tree-summary').textContent=`Plex 媒体库里的文件夹共有 ${number(c.total)} 个视频，占用 ${sizeText(c.size)}${c.unindexed?`，其中 ${number(c.unindexed)} 个 Plex 还没有收录`:'，Plex 全部已收录'}。数量包含所有子文件夹，点击可以展开下一层。`;
    $('#tree-root').innerHTML=treeLevelHTML(data,0);
  }catch(error){$('#tree-root').innerHTML='';onError(error);}
}
$('#tree-button').onclick=openTree;
$('#tree-root').onclick=async event=>{
  const file=event.target.closest('[data-tree-item]');
  if(file){openDetail(file.dataset.treeItem);return;}
  const row=event.target.closest('[data-tree-path]');if(!row||row.disabled)return;
  const node=row.parentElement;const children=$('.tree-children',node);const open=node.getAttribute('aria-expanded')==='true';
  node.setAttribute('aria-expanded',String(!open));$('.tree-caret',row).textContent=open?'▸':'▾';children.hidden=open;
  if(open||children.dataset.loaded)return;
  children.innerHTML='<li class="tree-more">正在读取…</li>';
  try{const path=JSON.parse(row.dataset.treePath);children.innerHTML=treeLevelHTML(await treeLevel(path),path.length);children.dataset.loaded='1';}
  catch(error){children.innerHTML='';node.setAttribute('aria-expanded','false');$('.tree-caret',row).textContent='▸';children.hidden=true;onError(error);}
};
