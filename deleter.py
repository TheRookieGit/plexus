"""Delete movies and episodes from disk: a whole folder when it holds nothing else, otherwise the file and its companions.

Nothing goes to the Recycle Bin (a network drive has none), so every delete is shown first and confirmed twice in the page."""
from __future__ import annotations

import ntpath
import os
import secrets
import shutil
import stat
import sys
import threading
import time

from disk import VIDEO, norm

PREVIEW_SECONDS = 600
SCOPES = {'item','season','episode','file'}


def inside(path,folder):
    return norm(path).startswith(norm(folder)+'\\')


def companions(video):
    """Subtitles, .nfo and artwork named after a video, such as 'Movie.chs.ass' or 'Movie-poster.jpg' beside 'Movie.mkv'."""
    folder,name = ntpath.split(video)
    stem = ntpath.splitext(name)[0].casefold()
    found = []
    try:entries = list(os.scandir(folder))
    except OSError:return found
    for entry in entries:
        lower = entry.name.casefold()
        if entry.is_file() and lower!=name.casefold() and ntpath.splitext(lower)[1] not in VIDEO and lower.startswith(stem) and lower[len(stem):len(stem)+1] in {'.','-','_',' '}:
            found.append(entry.path)
    return found


def measure(path):
    """(files, bytes, videos, a few other file names) below a folder, or for one file."""
    if not ntpath.isdir(path):
        return 1,os.stat(path).st_size,int(ntpath.splitext(path)[1].lower() in VIDEO),[]
    files = size = videos = 0
    others = []
    for folder,_,names in os.walk(path):
        for name in names:
            files += 1
            try:size += os.stat(ntpath.join(folder,name)).st_size
            except OSError:pass
            if ntpath.splitext(name)[1].lower() in VIDEO:videos += 1
            elif len(others)<8 and not name.startswith('.'):others.append(name)
    return files,size,videos,others


def videos_under(folder):
    for root,_,names in os.walk(folder):
        for name in names:
            if ntpath.splitext(name)[1].lower() in VIDEO and not name.startswith('.'):
                yield norm(ntpath.join(root,name))


def force_remove(function,path,_):
    # Read-only files (common on files copied from discs or NAS shares) cannot be removed until writable.
    os.chmod(path,stat.S_IWRITE)
    function(path)


class Deleter:
    def __init__(self,lib):
        self.lib = lib
        self.previews = {}
        self.background = True  # Tests wait for Plex to be told.
        self.lock = threading.Lock()
        with lib.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS deletions (id INTEGER PRIMARY KEY,time REAL NOT NULL,title TEXT NOT NULL,
                          path TEXT NOT NULL,kind TEXT NOT NULL,size INTEGER NOT NULL,error TEXT NOT NULL)''')

    def _locations(self):
        with self.lib.db() as db:
            rows = db.execute('SELECT id,locations FROM sections').fetchall()
        return [(location,row['id']) for row in rows for location in row['locations'].split('\n') if location and ntpath.isdir(location)]

    def _targets(self,item_id,scope,season=None,episode_id='',path=''):
        if scope not in SCOPES:raise ValueError('不支持的删除范围。')
        item = self.lib.catalog.item(item_id)
        rows = [m for m in item['media'] if m['state']=='ok']
        if scope=='season':rows = [m for m in rows if m['episode_id'] and m['season']==int(season)]
        elif scope=='episode':rows = [m for m in rows if m['episode_id']==str(episode_id)]
        elif scope=='file':rows = [m for m in rows if norm(m['path'])==norm(path)]
        if not rows:raise ValueError('没有找到要删除的文件，可能已经删除，请重新同步。')
        return item,[m['path'] for m in rows]

    def plan(self,item_id,scope,season=None,episode_id='',path=''):
        """The folders and files a delete removes. A folder goes as a whole only when every video in it is being deleted."""
        item,paths = self._targets(item_id,scope,season,episode_id,path)
        chosen = {norm(p) for p in paths}
        locations = self._locations()
        removals = {}
        for video in paths:
            home = next(((l,s) for l,s in locations if inside(video,l)),None)
            if not home:raise ValueError(f'{video} 不在 Plex 媒体库的文件夹里，Plexus 不会删除它。')
            if not ntpath.isfile(video):continue
            folder,best = ntpath.dirname(video),None
            while inside(folder,home[0]) and set(videos_under(folder))<=chosen:
                best,folder = folder,ntpath.dirname(folder)
            if best:
                removals[norm(best)] = (best,'folder',home[1])
            else:
                for target in [video,*companions(video)]:
                    removals.setdefault(norm(target),(target,'file',home[1]))
        # A folder being removed already takes everything inside it.
        folders = [key for key,(_,kind,_) in removals.items() if kind=='folder']
        removals = {key:value for key,value in removals.items() if not any(key!=f and key.startswith(f+'\\') for f in folders)}
        if not removals:raise ValueError('这些文件已经不在硬盘上了，请重新同步。')
        targets = []
        for target,kind,section in sorted(removals.values(),key=lambda r:norm(r[0])):
            files,size,videos,others = measure(target)
            targets.append({'path':target,'kind':kind,'section':section,'files':files,'size':size,'videos':videos,'others':others})
        return item,targets

    def preview(self,data):
        args = (str(data['id']),str(data.get('scope','item')),data.get('season'),str(data.get('episode_id','')),str(data.get('path','')))
        item,targets = self.plan(*args)
        token = secrets.token_urlsafe(16)
        now = time.time()
        with self.lock:
            self.previews = {k:v for k,v in self.previews.items() if v['expires']>now}
            self.previews[token] = {'args':args,'paths':[t['path'] for t in targets],'title':item['title'],'expires':now+PREVIEW_SECONDS}
        return {'token':token,'title':item['title'],'targets':targets,'size':sum(t['size'] for t in targets),'files':sum(t['files'] for t in targets)}

    def confirm(self,token):
        with self.lock:
            preview = self.previews.pop(str(token or ''),None)
        if not preview or preview['expires']<time.time():raise ValueError('确认已过期，请重新点击删除。')
        _,targets = self.plan(*preview['args'])
        if [t['path'] for t in targets]!=preview['paths']:raise ValueError('要删除的文件有变化，请重新确认。')
        deleted,failed = [],[]
        for target in targets:
            try:
                if target['kind']=='folder':shutil.rmtree(target['path'],**{'onexc' if sys.version_info>=(3,12) else 'onerror':force_remove})
                else:
                    try:os.remove(target['path'])
                    except PermissionError:force_remove(os.remove,target['path'],None)
                deleted.append(target)
                error = ''
            except OSError as exc:
                error = exc.strerror or str(exc)
                failed.append({'path':target['path'],'error':error})
            with self.lib.db() as db:
                db.execute('INSERT INTO deletions (time,title,path,kind,size,error) VALUES (?,?,?,?,?,?)',
                           (time.time(),preview['title'],target['path'],target['kind'],target['size'],error))
        self.lib.forget_paths([t['path'] for t in deleted])
        if deleted:
            self.lib.start_sync()
            if self.background:threading.Thread(target=self._tell_plex,args=(deleted,),daemon=True).start()
            else:self._tell_plex(deleted)
        return {'deleted':len(deleted),'size':sum(t['size'] for t in deleted),'failed':failed}

    def _tell_plex(self,deleted):
        """Let Plex notice the files are gone and drop them, then refresh the catalog."""
        try:
            plex = self.lib.plex()
            sections = {}
            for target in deleted:sections.setdefault(target['section'],set()).add(ntpath.dirname(target['path']))
            for section,folders in sections.items():
                for folder in folders:plex.refresh(section,folder)
            plex.wait_idle()
            for section in sections:plex.empty_trash(section)
            plex.wait_idle()
        except Exception:  # Plex catches up on its own next scan; the catalog still hides the deleted files.
            pass
        for _ in range(120):
            if self.lib.start_sync():break
            time.sleep(1)

    def history(self):
        with self.lib.db() as db:
            return [dict(r) for r in db.execute('SELECT * FROM deletions ORDER BY id DESC LIMIT 200')]
