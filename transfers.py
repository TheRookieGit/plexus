"""Saving share links into your own drive: paste a link, choose its files, and they land in a Plexus转存 folder there.

Plexus stops at the drive. Quark limits third-party downloads to about 100 KB/s a connection, so the files are
downloaded by hand in the drive's own client."""
from __future__ import annotations

import json
import threading
import time
import uuid

from netdisk import FOLDER, NetDiskError, links, wanted

RESOLVE_SECONDS = 1800
SCHEMA = '''
DROP TABLE IF EXISTS dl_files;
DROP TABLE IF EXISTS dl_batches;
CREATE TABLE IF NOT EXISTS transfers (
  id TEXT PRIMARY KEY,created REAL NOT NULL,drive TEXT NOT NULL,link TEXT NOT NULL,title TEXT NOT NULL,state TEXT NOT NULL,
  error TEXT NOT NULL,share TEXT NOT NULL,files TEXT NOT NULL,finished REAL);
'''
WAIT_LOGIN = ' 重新登录后会自动继续转存。'


class Transfers:
    def __init__(self,lib):
        self.lib = lib
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.resolved = {}       # Shares read from pasted links, waiting for files to be chosen.
        self.saving = set()      # transfer ids being saved into your drive
        self.started = False
        with lib.db() as db:
            db.executescript(SCHEMA)
            # Whatever was running when Plexus stopped picks up again.
            db.execute("UPDATE transfers SET state='queued' WHERE state='saving'")

    # The worker ---------------------------------------------------------------------------------------------------

    def start(self):
        if not self.started:
            self.started = True
            threading.Thread(target=self._loop,daemon=True,name='transfers').start()

    def _loop(self):
        while True:
            try:self.tick()
            except Exception:pass  # One bad transfer must not stop the queue; its own error is recorded where it failed.
            self.wake.wait(2)
            self.wake.clear()

    def tick(self):
        """Start saving whatever is queued. Work for a drive that is signed out (here or in another app) waits;
        signing in again anywhere resumes it."""
        with self.lib.db() as db:
            queued = [dict(r) for r in db.execute("SELECT id,drive FROM transfers WHERE state='queued' ORDER BY created")]
        signed = {key:drive.signed_in for key,drive in self.lib.netdisk.drives.items()}
        for row in queued:
            with self.lock:
                if not signed.get(row['drive']) or row['id'] in self.saving:continue
                self.saving.add(row['id'])
            self._run(self._save,row['id'])

    def _run(self,target,*args):
        if not getattr(self,'background',True):
            target(*args);return
        threading.Thread(target=target,args=args,daemon=True).start()

    # Database helpers ---------------------------------------------------------------------------------------------

    def update(self,transfer_id,**fields):
        with self.lib.db() as db:
            db.execute(f'UPDATE transfers SET {",".join(f"{k}=?" for k in fields)} WHERE id=?',(*fields.values(),transfer_id))

    def transfer(self,transfer_id):
        with self.lib.db() as db:
            row = db.execute('SELECT * FROM transfers WHERE id=?',(transfer_id,)).fetchone()
        if not row:raise KeyError('找不到这条转存记录。')
        return dict(row)|{key:json.loads(row[key]) for key in ('link','share','files')}

    # Adding links -------------------------------------------------------------------------------------------------

    def resolve(self,text):
        """Read every share link in pasted text and list what each holds, so you can choose what to save."""
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
            shares.append({'token':token,'drive':drive.key,'label':drive.label,'title':title,'files':listed})
        return {'shares':shares}

    def add(self,token,indexes):
        resolved = self.resolved.get(str(token))
        if not resolved:raise ValueError('分享链接的读取结果已过期，请重新粘贴链接。')
        chosen = [resolved['files'][i] for i in sorted({int(i) for i in indexes}) if 0<=int(i)<len(resolved['files'])]
        if not chosen:raise ValueError('请至少选择一个文件。')
        transfer_id = uuid.uuid4().hex[:12]
        files = [f|{'state':'waiting','error':''} for f in chosen]
        with self.lib.db() as db:
            db.execute('INSERT INTO transfers VALUES (?,?,?,?,?,?,?,?,?,?)',
                       (transfer_id,time.time(),resolved['drive'],json.dumps(resolved['link']),resolved['title'][:200],'queued','',
                        json.dumps(resolved['share'],ensure_ascii=False),json.dumps(files,ensure_ascii=False),None))
        self.wake.set()
        return {'id':transfer_id}

    # Saving into your drive ---------------------------------------------------------------------------------------

    def _store(self,drive,share,files):
        """Save the files still waiting, marking each as its result arrives, so a failure part way repeats none of them."""
        waiting = [f for f in files if f['state']=='waiting']
        if not waiting:return
        for item,result in drive.save(share,waiting):
            item['state'],item['error'] = ('failed',result) if isinstance(result,str) else ('saved','')

    def _save(self,transfer_id):
        files,outcome = [],{}
        try:
            row = self.transfer(transfer_id)
            if row['state']!='queued':return
            files = row['files']
            self.update(transfer_id,state='saving',error='')
            drive = self.lib.netdisk.drive(row['drive'])
            try:self._store(drive,row['share'],files)
            except NetDiskError as exc:
                if exc.login:raise
                # The share's access token may have expired since the links were read; read the share again once.
                share,listed = drive.files(row['link'])
                fresh = {f['path']:f for f in listed}
                for f in files:f.update({k:v for k,v in fresh.get(f['path'],{}).items() if k.startswith('_')})
                outcome['share'] = json.dumps(share,ensure_ascii=False)
                self._store(drive,share,files)
            failed = [f for f in files if f['state']=='failed']
            if failed:outcome |= {'state':'failed','error':f'{len(failed)} 个文件没能转存：{failed[0]["error"]}'}
            else:outcome |= {'state':'done','finished':time.time()}
        except NetDiskError as exc:
            # A lapsed sign-in is not the transfer's fault: it waits, queued, until the drive is signed in again.
            outcome |= {'state':'queued','error':str(exc)+WAIT_LOGIN} if exc.login else {'state':'failed','error':str(exc)}
        except (KeyError,TypeError,ValueError,AttributeError):
            outcome |= {'state':'failed','error':'网盘的接口可能变了，没能转存。'}
        finally:
            if outcome:self.update(transfer_id,files=json.dumps(files,ensure_ascii=False),**outcome)
            with self.lock:self.saving.discard(transfer_id)
            self.wake.set()

    # Controls -----------------------------------------------------------------------------------------------------

    def control(self,data):
        action = data.get('action')
        transfer_id = str(data.get('id',''))
        row = self.transfer(transfer_id)
        if action=='retry':
            if row['state']!='failed':raise ValueError('这条转存不需要重试。')
            files = [f|{'state':'waiting','error':''} if f['state']=='failed' else f for f in row['files']]
            self.update(transfer_id,state='queued',error='',files=json.dumps(files,ensure_ascii=False))
        elif action=='remove':
            # Only the record goes: what was saved stays in your drive.
            if row['state']=='saving':raise ValueError('正在转存，请等它完成。')
            with self.lib.db() as db:
                db.execute('DELETE FROM transfers WHERE id=?',(transfer_id,))
            return {'ok':True}
        else:
            raise ValueError('不支持的操作。')
        self.wake.set()
        return self.public(transfer_id)

    # What the page shows ------------------------------------------------------------------------------------------

    def public(self,transfer_id):
        row = self.transfer(transfer_id)
        drive = self.lib.netdisk.drive(row['drive'])
        files = [{k:f[k] for k in ('path','size','state','error')} for f in row['files']]
        return {k:row[k] for k in ('id','created','drive','title','state','error','finished')}|{
            'files':files,'size':sum(f['size'] for f in files),'label':drive.label,'folder':FOLDER,
            # Waiting for a sign-in: still queued, but its drive is signed out.
            'login':drive.key if row['state']=='queued' and not drive.signed_in else ''}

    def listing(self):
        with self.lib.db() as db:
            ids = [r[0] for r in db.execute('SELECT id FROM transfers ORDER BY created DESC LIMIT 100')]
        return {'transfers':[self.public(i) for i in ids],'netdisk':self.lib.netdisk.status()}

    def summary(self):
        with self.lib.db() as db:
            active = db.execute("SELECT COUNT(*) FROM transfers WHERE state IN ('queued','saving')").fetchone()[0]
        return {'active':active}
