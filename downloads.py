"""The download queue: share links in, Plex-named files out.

A batch is one share link. Its chosen files are saved into your own drive, then downloaded one by one into a hidden
staging folder on the destination drive, resuming where they stopped after a restart or a dropped connection. When
every file has arrived, Plexus proposes Plex names; nothing is moved into the library until you confirm."""
from __future__ import annotations

import json
import ntpath
import os
import shutil
import threading
import time
import uuid

from disk import norm
from naming import guess, kind_of, plan, rank, safe, search
from netdisk import NetDiskError, Unreachable, links, wanted

STAGING = '.plexus-downloads'
SLOTS = 2
ATTEMPTS = 6
RESOLVE_SECONDS = 1800
SCHEMA = '''
CREATE TABLE IF NOT EXISTS dl_batches (
  id TEXT PRIMARY KEY,created REAL NOT NULL,drive TEXT NOT NULL,link TEXT NOT NULL,title TEXT NOT NULL,location TEXT NOT NULL,
  section TEXT NOT NULL,kind TEXT NOT NULL,state TEXT NOT NULL,error TEXT NOT NULL,share TEXT NOT NULL,
  identity TEXT NOT NULL,plan TEXT NOT NULL,finished REAL);
CREATE TABLE IF NOT EXISTS dl_files (
  id TEXT PRIMARY KEY,batch_id TEXT NOT NULL,position INTEGER NOT NULL,path TEXT NOT NULL,size INTEGER NOT NULL,
  done INTEGER NOT NULL,state TEXT NOT NULL,error TEXT NOT NULL,private TEXT NOT NULL,ref TEXT NOT NULL,attempts INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS dl_files_batch ON dl_files(batch_id);
'''
FINISHED = {'done','failed','canceled'}


class Stopped(Exception):
    """A download was paused or cancelled while it ran."""


class Sink:
    """Receives a download's bytes: writes them, counts progress, and stops when asked to."""
    def __init__(self,manager,file_id,out,done):
        self.manager,self.file_id,self.out,self.done = manager,file_id,out,done
        self.saved = time.monotonic()

    def write(self,chunk):
        if self.file_id in self.manager.stopping:
            raise Stopped()
        self.out.write(chunk)
        self.done += len(chunk)
        self.manager.progress(self.file_id,self.done)
        if time.monotonic()-self.saved>3:
            self.saved = time.monotonic()
            self.manager.update_file(self.file_id,done=self.done)


def hide(path):
    if os.name=='nt':
        import ctypes
        ctypes.windll.kernel32.SetFileAttributesW(str(path),0x2)


class Downloads:
    def __init__(self,lib):
        self.lib = lib
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.resolved = {}       # Shares read from pasted links, waiting for files to be chosen.
        self.active = {}         # file id -> thread downloading it
        self.saving = set()      # batch ids being saved into your drive
        self.stopping = set()    # file ids asked to stop
        self.speed = {}          # file id -> [(time, bytes), …] for the last few seconds
        self.retry_at = {}       # file id -> when to try again after an error
        self.started = False
        self.staging_base = ''   # Tests download somewhere other than the top of the drive.
        with lib.db() as db:
            db.executescript(SCHEMA)
            # Whatever was running when Plexus stopped picks up again.
            db.execute("UPDATE dl_files SET state='ready' WHERE state='downloading'")
            db.execute("UPDATE dl_batches SET state='queued' WHERE state='saving'")
            db.execute("UPDATE dl_batches SET state='review',error='' WHERE state='applying'")

    # The worker ---------------------------------------------------------------------------------------------------

    def start(self):
        if not self.started:
            self.started = True
            threading.Thread(target=self._loop,daemon=True,name='downloads').start()

    def _loop(self):
        while True:
            try:self.tick()
            except Exception:pass  # One bad batch must not stop the queue; its own error is recorded where it failed.
            self.wake.wait(2)
            self.wake.clear()

    def tick(self):
        """Start whatever can start now: saving queued batches, downloading saved files, and reviewing finished batches."""
        with self.lib.db() as db:
            queued = [r[0] for r in db.execute("SELECT id FROM dl_batches WHERE state='queued' ORDER BY created")]
            ready = [dict(r) for r in db.execute('''SELECT f.* FROM dl_files f JOIN dl_batches b ON b.id=f.batch_id
                     WHERE f.state='ready' AND b.state='downloading' ORDER BY b.created,f.position''')]
            downloading = [r[0] for r in db.execute("SELECT id FROM dl_batches WHERE state='downloading'")]
        with self.lock:
            for batch_id in queued:
                if batch_id not in self.saving:
                    self.saving.add(batch_id)
                    self._run(self._save,batch_id)
            now = time.time()
            for row in ready:
                if len(self.active)>=SLOTS:break
                if row['id'] in self.active or self.retry_at.get(row['id'],0)>now:continue
                self.active[row['id']] = True
                self._run(self._download,row['id'])
        for batch_id in downloading:
            self._maybe_finish(batch_id)

    def _run(self,target,*args):
        if not getattr(self,'background',True):
            target(*args);return None
        thread = threading.Thread(target=target,args=args,daemon=True)
        thread.start()
        return thread

    # Database helpers ---------------------------------------------------------------------------------------------

    def update_file(self,file_id,**fields):
        with self.lib.db() as db:
            db.execute(f'UPDATE dl_files SET {",".join(f"{k}=?" for k in fields)} WHERE id=?',(*fields.values(),file_id))

    def update_batch(self,batch_id,**fields):
        with self.lib.db() as db:
            db.execute(f'UPDATE dl_batches SET {",".join(f"{k}=?" for k in fields)} WHERE id=?',(*fields.values(),batch_id))

    def batch(self,batch_id):
        with self.lib.db() as db:
            row = db.execute('SELECT * FROM dl_batches WHERE id=?',(batch_id,)).fetchone()
            if not row:raise KeyError('找不到这个下载任务。')
            files = [dict(r) for r in db.execute('SELECT * FROM dl_files WHERE batch_id=? ORDER BY position',(batch_id,))]
        return dict(row),files

    def progress(self,file_id,done):
        now = time.monotonic()
        samples = [s for s in self.speed.get(file_id,[]) if now-s[0]<6]+[(now,done)]
        self.speed[file_id] = samples

    def rate(self,file_id):
        samples = self.speed.get(file_id,[])
        if len(samples)<2 or time.monotonic()-samples[-1][0]>5:return 0
        return int((samples[-1][1]-samples[0][1])/max(.5,samples[-1][0]-samples[0][0]))

    # Adding links -------------------------------------------------------------------------------------------------

    def resolve(self,text):
        """Read every share link in pasted text and list what each holds, so you can choose what to download."""
        found = links(text)
        if not found:raise NetDiskError('没有找到夸克网盘或百度网盘的分享链接。')
        for link in found:
            drive = self.lib.netdisk.drive(link['drive'])
            if not drive.signed_in:raise NetDiskError(f'请先用{drive.app}扫码登录{drive.label}。',login=drive.key)
        now = time.time()
        self.resolved = {k:v for k,v in self.resolved.items() if v['expires']>now}
        shares = []
        for link in found:
            drive = self.lib.netdisk.drive(link['drive'])
            try:share,files = drive.files(link)
            except NetDiskError as exc:
                if exc.login:raise
                shares.append({'drive':drive.key,'label':drive.label,'error':str(exc),'files':[]});continue
            token = uuid.uuid4().hex[:16]
            listed = [{'index':i,'path':f['path'],'size':f['size'],'kind':wanted(f['name'],f['size'])} for i,f in enumerate(files)]
            top = {f['path'].split('/')[0] for f in files}
            title = share.get('title') or (next(iter(top)) if len(top)==1 else files[0]['name'] if files else '')
            self.resolved[token] = {'drive':drive.key,'link':link,'share':share,'files':files,'title':title,'expires':now+RESOLVE_SECONDS}
            shares.append({'token':token,'drive':drive.key,'label':drive.label,'title':title,'files':listed,'guess':guess([f['path'] for f in files if wanted(f['name'],f['size'])],title)})
        return {'shares':shares,'locations':self.locations()}

    def locations(self):
        """Library folders that are connected, with free space, to choose where a download goes."""
        with self.lib.db() as db:
            rows = db.execute('SELECT id,kind,title,locations FROM sections ORDER BY CAST(id AS INTEGER)').fetchall()
        result = []
        for row in rows:
            for location in filter(None,row['locations'].split('\n')):
                if not ntpath.isdir(location):continue
                try:free = shutil.disk_usage(location).free
                except OSError:free = 0
                result.append({'path':location,'section':row['id'],'title':row['title'],'kind':row['kind'],'drive':ntpath.splitdrive(location)[0].upper(),'free':free})
        return result

    def add(self,token,indexes,location):
        resolved = self.resolved.get(str(token))
        if not resolved:raise ValueError('分享链接的读取结果已过期，请重新粘贴链接。')
        place = next((l for l in self.locations() if norm(l['path'])==norm(location)),None)
        if not place:raise ValueError('请选择一个 Plex 媒体库的文件夹。')
        chosen = [resolved['files'][i] for i in sorted({int(i) for i in indexes}) if 0<=int(i)<len(resolved['files'])]
        if not chosen:raise ValueError('请至少选择一个文件。')
        size = sum(f['size'] for f in chosen)
        if size>place['free']:raise ValueError(f'{place["drive"]} 盘剩余空间不足（需要 {size/2**30:.1f} GB）。')
        batch_id = uuid.uuid4().hex[:12]
        with self.lib.db() as db:
            db.execute('INSERT INTO dl_batches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (batch_id,time.time(),resolved['drive'],json.dumps(resolved['link']),resolved['title'][:200],place['path'],place['section'],
                        place['kind'],'queued','',json.dumps(resolved['share'],ensure_ascii=False),'{}','[]',None))
            for position,f in enumerate(chosen):
                private = {k:v for k,v in f.items() if k.startswith('_')}
                db.execute('INSERT INTO dl_files VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                           (uuid.uuid4().hex[:12],batch_id,position,f['path'],f['size'],0,'waiting','',json.dumps(private),'',0))
        self.wake.set()
        return {'id':batch_id}

    # Saving into your drive ---------------------------------------------------------------------------------------

    def _save(self,batch_id):
        try:
            batch,files = self.batch(batch_id)
            if batch['state']!='queued':return
            self.update_batch(batch_id,state='saving',error='')
            drive = self.lib.netdisk.drive(batch['drive'])
            pending = [f for f in files if f['state']=='waiting']
            if pending:
                share = json.loads(batch['share'])
                items = [{'path':f['path'],'size':f['size'],'name':f['path'].rsplit('/',1)[-1],'_id':f['id'],**json.loads(f['private'])} for f in pending]
                try:results = list(drive.save(share,items))
                except NetDiskError as exc:
                    if exc.login:raise
                    # The share's access token may have expired since the links were read; read the share again once.
                    share,listed = drive.files(json.loads(batch['link']))
                    fresh = {f['path']:f for f in listed}
                    items = [i|{k:v for k,v in fresh.get(i['path'],{}).items() if k.startswith('_')} for i in items]
                    self.update_batch(batch_id,share=json.dumps(share,ensure_ascii=False))
                    results = list(drive.save(share,items))
                for item,ref in results:
                    if isinstance(ref,str):self.update_file(item['_id'],state='failed',error=ref)
                    else:self.update_file(item['_id'],state='ready',ref=json.dumps(ref))
            self.update_batch(batch_id,state='downloading')
        except NetDiskError as exc:
            self.update_batch(batch_id,state='failed',error=str(exc)+(' 登录后点「重试」。' if exc.login else ''))
        except (KeyError,TypeError,ValueError,AttributeError):
            self.update_batch(batch_id,state='failed',error='网盘的接口可能变了，没能转存。')
        finally:
            with self.lock:self.saving.discard(batch_id)
            self.wake.set()

    # Downloading --------------------------------------------------------------------------------------------------

    def batch_folder(self,batch):
        """Where a batch downloads to: a hidden folder at the top of the destination drive, outside Plex's libraries,
        so moving into the library afterwards is a rename on the same drive."""
        return ntpath.join(self.staging_base or ntpath.splitdrive(batch['location'])[0]+'\\',STAGING,batch['id'])

    def staging(self,batch,path):
        root = ntpath.dirname(self.batch_folder(batch))
        if not ntpath.isdir(root):
            os.makedirs(root,exist_ok=True)
            hide(root)
        return ntpath.join(root,batch['id'],*[safe(p) for p in path.split('/')])

    def _download(self,file_id):
        try:
            with self.lib.db() as db:
                row = dict(db.execute('SELECT * FROM dl_files WHERE id=?',(file_id,)).fetchone())
            batch,_ = self.batch(row['batch_id'])
            if row['state']!='ready':return
            target = self.staging(batch,row['path'])
            part = target+'.part'
            os.makedirs(ntpath.dirname(target),exist_ok=True)
            if ntpath.isfile(target) and os.path.getsize(target)==row['size']:
                self.update_file(file_id,state='done',done=row['size'],error='');return
            self.update_file(file_id,state='downloading',error='')
            drive = self.lib.netdisk.drive(batch['drive'])
            for _ in range(2):
                start = os.path.getsize(part) if ntpath.isfile(part) else 0
                if start>row['size']:start = 0
                if start<row['size']:
                    with open(part,'ab' if start else 'wb') as out:
                        status = drive.stream(json.loads(row['ref']),start,Sink(self,file_id,out,start))
                    if status==200 and start:
                        # The drive sent the whole file instead of the rest, so the part is the start twice over: begin again.
                        os.remove(part);continue
                    if status not in (200,206):raise Unreachable(f'网盘返回 HTTP {status}，会自动重试。')
                break
            got = os.path.getsize(part)
            if got!=row['size']:raise Unreachable(f'下载不完整（{got}/{row["size"]} 字节），会自动重试。')
            os.replace(part,target)
            self.update_file(file_id,state='done',done=row['size'],error='',attempts=0)
        except Stopped:
            pass  # The pause or cancel already recorded the new state.
        except NetDiskError as exc:
            self._failed(file_id,str(exc),retry=not exc.login)
        except OSError as exc:
            self._failed(file_id,f'写入硬盘失败：{exc.strerror or exc}',retry=False)
        except (KeyError,TypeError,ValueError,AttributeError):
            self._failed(file_id,'网盘的接口可能变了，没能下载。',retry=False)
        finally:
            with self.lock:
                self.active.pop(file_id,None)
                self.stopping.discard(file_id)
            self.speed.pop(file_id,None)
            self.wake.set()

    def _failed(self,file_id,message,retry):
        with self.lib.db() as db:
            attempts = db.execute('SELECT attempts FROM dl_files WHERE id=?',(file_id,)).fetchone()[0]+1
        if retry and attempts<ATTEMPTS:
            self.retry_at[file_id] = time.time()+min(300,15*2**attempts)
            self.update_file(file_id,state='ready',error=message,attempts=attempts)
        else:
            self.update_file(file_id,state='failed',error=message,attempts=attempts)

    def _maybe_finish(self,batch_id):
        """Once every file has arrived (or given up), work out what the download is and propose Plex names."""
        batch,files = self.batch(batch_id)
        if any(f['state'] not in FINISHED and f['state']!='paused' for f in files):return
        if any(f['state']=='paused' for f in files):return
        done = [f for f in files if f['state']=='done']
        if not done:
            self.update_batch(batch_id,state='failed' if any(f['state']=='failed' for f in files) else 'canceled',
                              error=next((f['error'] for f in files if f['error']),''))
            return
        names = [f['path'] for f in done]
        identity = guess(names,batch['title'])|{'kind':batch['kind'] if batch['kind'] in {'movie','show'} else guess(names)['kind']}
        try:
            candidates = rank(search(self.lib.plex().token,identity['title'],identity['kind']),identity['title'],identity['year'])[:6]
        except Exception:  # Naming still works from the file names alone when Plex's online catalog cannot be reached.
            candidates = []
        # Plex's best match is taken when its title or year agrees; it is still only a proposal until confirmed.
        if candidates and (candidates[0]['title'].casefold()==identity['title'].casefold() or identity['year'] and candidates[0]['year']==identity['year']):
            best = candidates[0]
            identity |= {'title':best['title'],'year':best['year'] or identity['year'],'guid':best['guid']}
        identity['candidates'] = candidates
        self.update_batch(batch_id,state='review',identity=json.dumps(identity,ensure_ascii=False),
                          plan=json.dumps(self._plan(batch,done,identity),ensure_ascii=False))

    def _plan(self,batch,files,identity,overrides=None):
        overrides = overrides or {}
        existing = self._existing(identity.get('guid',''),batch['location'])
        rows = [{'id':f['id'],'path':f['path'],'size':f['size'],**overrides.get(f['id'],{})} for f in files]
        result = plan(rows,identity['kind'],identity['title'],identity.get('year'),batch['location'],existing)
        for row in result:
            row['source'] = self.staging(batch,row['path'])
            row['exists'] = ntpath.exists(row['target'])
        return result

    def _existing(self,guid,location):
        """The folder of a show or movie Plex already has in this library folder, so new episodes join it."""
        if not guid:return ''
        with self.lib.db() as db:
            rows = db.execute('SELECT m.path FROM media m JOIN items i ON i.id=m.item_id WHERE i.guid=?',(guid,)).fetchall()
        for row in rows:
            if norm(row[0]).startswith(norm(location)+'\\'):
                return ntpath.join(location,ntpath.relpath(row[0],location).split('\\')[0])
        return ''

    # Review and moving into the library ---------------------------------------------------------------------------

    def identify(self,data):
        """Change what a finished download is (title, year, movie or show, or an episode's number) and rebuild the names."""
        batch,files = self.batch(str(data['id']))
        if batch['state']!='review':raise ValueError('这个下载还不能重命名。')
        identity = json.loads(batch['identity'])
        title = str(data.get('title',identity['title'])).strip()
        if not title:raise ValueError('请填写片名。')
        year = int(data['year']) if str(data.get('year') or '').strip().isdigit() else None
        identity |= {'title':title,'year':year,'kind':data.get('kind') if data.get('kind') in {'movie','show'} else identity['kind'],'guid':str(data.get('guid',''))}
        overrides = {}
        for row in data.get('files') or []:
            values = {k:int(row[k]) for k in ('season','episode') if str(row.get(k,'')).strip().isdigit()}
            if values:overrides[str(row['id'])] = values
        done = [f for f in files if f['state']=='done']
        planned = self._plan(batch,done,identity,overrides)
        self.update_batch(batch['id'],identity=json.dumps(identity,ensure_ascii=False),plan=json.dumps(planned,ensure_ascii=False))
        return self.public(batch['id'])

    def apply(self,batch_id,rename=True):
        """Move the downloaded files into the library, under Plex names or as they were shared, and tell Plex."""
        batch,files = self.batch(batch_id)
        if batch['state']!='review':raise ValueError('这个下载已经处理过了。')
        self.update_batch(batch_id,state='applying')
        try:
            planned = json.loads(batch['plan'])
            if not rename:
                folder = ntpath.join(batch['location'],safe(batch['title'] or batch_id))
                top = {p['path'].split('/')[0] for p in planned}
                # A share that is one folder keeps that folder's name; loose files get one named after the share.
                base = batch['location'] if len(top)==1 and all('/' in p['path'] for p in planned) else folder
                for p in planned:p['target'] = ntpath.join(base,*[safe(x) for x in p['path'].split('/')])
            moved = []
            for p in planned:
                target = p['target']
                stem,ext = ntpath.splitext(target)
                count = 2
                while ntpath.exists(target):
                    target = f'{stem} ({count}){ext}';count += 1
                os.makedirs(ntpath.dirname(target),exist_ok=True)
                shutil.move(p['source'],target)
                moved.append(target)
            shutil.rmtree(self.batch_folder(batch),ignore_errors=True)
            self.update_batch(batch_id,state='done',finished=time.time(),plan=json.dumps([p|{'target':t} for p,t in zip(planned,moved)],ensure_ascii=False))
        except OSError as exc:
            self.update_batch(batch_id,state='review',error=f'移动文件失败：{exc.strerror or exc}')
            raise ValueError(f'移动文件失败：{exc.strerror or exc}') from exc
        folders = {ntpath.join(batch['location'],ntpath.relpath(t,batch['location']).split('\\')[0]) for t in moved}
        threading.Thread(target=self._tell_plex,args=(batch['section'],folders),daemon=True).start()
        return self.public(batch_id)

    def _tell_plex(self,section,folders):
        try:
            plex = self.lib.plex()
            for folder in folders:plex.refresh(section,folder)
            plex.wait_idle()
        except Exception:  # Plex finds the files on its own next scan.
            pass
        for _ in range(120):
            if self.lib.start_sync():break
            time.sleep(1)

    # Controls -----------------------------------------------------------------------------------------------------

    def control(self,data):
        action = data.get('action')
        if data.get('file'):
            with self.lib.db() as db:
                row = db.execute('SELECT batch_id FROM dl_files WHERE id=?',(str(data['file']),)).fetchone()
            if not row:raise KeyError('找不到这个文件。')
            batch_id,ids = row[0],[str(data['file'])]
        else:
            batch_id = str(data.get('batch',''))
            ids = None
        batch,files = self.batch(batch_id)
        chosen = [f for f in files if ids is None or f['id'] in ids]
        staging = self.batch_folder(batch)
        if action=='pause':
            for f in chosen:
                if f['state'] in {'ready','downloading'}:
                    self.stop(f['id']);self.update_file(f['id'],state='paused')
        elif action=='resume':
            for f in chosen:
                if f['state']=='paused':
                    self.update_file(f['id'],state='ready');self.retry_at.pop(f['id'],None)
        elif action=='retry':
            for f in chosen:
                if f['state']=='failed':
                    self.update_file(f['id'],state='ready' if f['ref'] else 'waiting',error='',attempts=0)
                    self.retry_at.pop(f['id'],None)
            # Files never saved into your drive go back through saving; the rest download straight away.
            if batch['state'] in {'failed','review'}:
                with self.lib.db() as db:
                    waiting = db.execute("SELECT COUNT(*) FROM dl_files WHERE batch_id=? AND state='waiting'",(batch_id,)).fetchone()[0]
                self.update_batch(batch_id,state='queued' if waiting else 'downloading',error='')
        elif action=='cancel':
            for f in chosen:
                # Cancelling a whole batch also discards files already downloaded but not yet moved into the library.
                if f['state']!='canceled' and (f['state']!='done' or ids is None):
                    self.stop(f['id'])
                    self.update_file(f['id'],state='canceled')
            if ids is None:
                self.update_batch(batch_id,state='canceled',finished=time.time())
                threading.Timer(3,shutil.rmtree,(staging,True)).start()
            else:
                for f in chosen:
                    part = self.staging(batch,f['path'])
                    threading.Timer(3,lambda p=part:[os.remove(x) for x in (p,p+'.part') if os.path.exists(x)]).start()
        elif action=='remove':
            if batch['state'] not in {'done','canceled','failed'}:raise ValueError('请先取消这个下载。')
            with self.lib.db() as db:
                db.execute('DELETE FROM dl_files WHERE batch_id=?',(batch_id,))
                db.execute('DELETE FROM dl_batches WHERE id=?',(batch_id,))
            shutil.rmtree(staging,ignore_errors=True)
            return {'ok':True}
        else:
            raise ValueError('不支持的操作。')
        self.wake.set()
        return self.public(batch_id)

    def stop(self,file_id):
        """Ask a running download to stop at its next chunk."""
        with self.lock:
            if file_id in self.active:self.stopping.add(file_id)

    # What the page shows ------------------------------------------------------------------------------------------

    def public(self,batch_id):
        batch,files = self.batch(batch_id)
        for f in files:
            f.pop('private');f.pop('ref')
            f['kind'] = kind_of(f['path'])
            f['speed'] = self.rate(f['id']) if f['state']=='downloading' else 0
            if f['state']=='downloading' and self.speed.get(f['id']):f['done'] = self.speed[f['id']][-1][1]
            f['retry_in'] = max(0,int(self.retry_at.get(f['id'],0)-time.time())) if f['state']=='ready' and f['error'] else 0
        identity = json.loads(batch['identity'])
        result = {k:batch[k] for k in ('id','created','drive','title','location','section','kind','state','error','finished')}
        result |= {'files':files,'identity':identity,'plan':json.loads(batch['plan']),'size':sum(f['size'] for f in files),
                   'done':sum(f['done'] for f in files if f['state']!='canceled'),'speed':sum(f['speed'] for f in files),
                   'label':self.lib.netdisk.drive(batch['drive']).label}
        return result

    def listing(self):
        with self.lib.db() as db:
            ids = [r[0] for r in db.execute('SELECT id FROM dl_batches ORDER BY created DESC LIMIT 100')]
        batches = [self.public(i) for i in ids]
        return {'batches':batches,'netdisk':self.lib.netdisk.status(),'locations':self.locations()}

    def applying(self):
        with self.lib.db() as db:
            return bool(db.execute("SELECT 1 FROM dl_batches WHERE state='applying'").fetchone())

    def summary(self):
        with self.lib.db() as db:
            active = db.execute("SELECT COUNT(*) FROM dl_batches WHERE state IN ('queued','saving','downloading')").fetchone()[0]
            review = db.execute("SELECT COUNT(*) FROM dl_batches WHERE state='review'").fetchone()[0]
        return {'active':active,'review':review,'speed':sum(self.rate(f) for f in list(self.active))}
