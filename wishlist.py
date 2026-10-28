"""Favourites among what is owned, and a wishlist of titles still to get, each with the share links to download it from.

Connected to the wish hub (心愿中枢, the my-collections site), the wishlist is the hub's movies and series instead:
kept here in hub_wishes, owned as before by Plex's guid or by title and year, and each title Plex has is reported
back so the hub shows it as owned. A match the hub marks 「不是这个」 is passed over. The wishlist kept here before
connecting goes up to the hub once and stays as it was, for going back. Favourites stay here."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import threading
import time
import urllib.parse
import uuid

from collectionkit.hub import Hub, HubError
from naming import rank, search

WEB = 'https://therookiegit.github.io/plexus/'  # Where the hub's 「查看」 opens an owned title.
PULL_SECONDS = 60
# A wish is owned once the catalog has files of the same Plex title, or of the same title within a year.
OWNED = '''SELECT id,title,guid FROM items WHERE files>0 AND ((?<>'' AND guid=?) OR (title=? AND (? IS NULL OR year IS NULL OR ABS(year-?)<=1)))
           ORDER BY source='plex' DESC LIMIT 20'''


def iso(seconds):
    return datetime.fromtimestamp(seconds,timezone.utc).isoformat().replace('+00:00','Z')


def seconds(value,default):
    try:return datetime.fromisoformat(str(value).replace('Z','+00:00')).timestamp()
    except ValueError:return default


class Wishlist:
    def __init__(self,lib,hub=None):
        self.lib = lib
        self.hub = hub or Hub('plexus')
        self.pulled = 0
        self.pulling = threading.Lock()
        with lib.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS favorites (key TEXT PRIMARY KEY,added REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS wishlist (id TEXT PRIMARY KEY,title TEXT NOT NULL,year INTEGER,kind TEXT NOT NULL,
              guid TEXT NOT NULL,thumb TEXT NOT NULL,summary TEXT NOT NULL,links TEXT NOT NULL,note TEXT NOT NULL,added REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS hub_wishes (id TEXT PRIMARY KEY,title TEXT NOT NULL,year INTEGER,kind TEXT NOT NULL,
              guid TEXT NOT NULL,thumb TEXT NOT NULL,summary TEXT NOT NULL,links TEXT NOT NULL,note TEXT NOT NULL,added REAL NOT NULL,
              data TEXT NOT NULL,rejected TEXT NOT NULL DEFAULT '[]',reported TEXT NOT NULL DEFAULT '');
            ''')

    def favorite(self,item_id,on):
        """Favourites follow Plex's identity of the title, so they survive moving files between libraries."""
        with self.lib.db() as db:
            row = db.execute('SELECT guid FROM items WHERE id=?',(item_id,)).fetchone()
            if not row:raise KeyError('这个条目已不在目录中，请重新同步。')
            key = row['guid'] or item_id
            if on:db.execute('INSERT OR REPLACE INTO favorites VALUES (?,?)',(key,time.time()))
            else:db.execute('DELETE FROM favorites WHERE key=?',(key,))
        return {'favorite':bool(on)}

    @property
    def table(self):
        return 'hub_wishes' if self.hub.paired else 'wishlist'

    @staticmethod
    def owned(db,wish):
        """The catalog item that has this wish, passing over ones the hub said are not it."""
        rejected = set(json.loads(wish.get('rejected') or '[]'))
        for row in db.execute(OWNED,(wish['guid'],wish['guid'],wish['title'],wish['year'],wish['year'])):
            if (row['guid'] or row['id']) not in rejected:return row
        return None

    def items(self):
        self.refresh_soon()
        with self.lib.db() as db:
            rows = [dict(r) for r in db.execute(f'SELECT * FROM {self.table} ORDER BY added DESC')]
            for row in rows:
                owned = self.owned(db,row)
                row['owned'] = owned['id'] if owned else ''
                for key in ('data','rejected','reported'):row.pop(key,None)
        return {'items':rows,'hub':self.hub.paired}

    def save(self,data):
        title = str(data.get('title','')).strip()[:200]
        if not title:raise ValueError('请填写片名。')
        year = int(data['year']) if str(data.get('year') or '').strip().isdigit() else None
        kind = data.get('kind') if data.get('kind') in {'movie','show'} else 'movie'
        wish_id = str(data.get('id') or '') or uuid.uuid4().hex[:12]
        values = (wish_id,title,year,kind,str(data.get('guid',''))[:200],str(data.get('thumb',''))[:500],str(data.get('summary',''))[:600],
                  str(data.get('links','')).strip()[:5000],str(data.get('note','')).strip()[:1000])
        if self.hub.paired:
            self.save_to_hub(values)
            return {'id':wish_id}
        with self.lib.db() as db:
            added = db.execute('SELECT added FROM wishlist WHERE id=?',(wish_id,)).fetchone()
            db.execute('INSERT OR REPLACE INTO wishlist VALUES (?,?,?,?,?,?,?,?,?,?)',(*values,added[0] if added else time.time()))
        return {'id':wish_id}

    def delete(self,wish_id):
        wish_id = str(wish_id)
        if self.hub.paired:
            with self.lib.db() as db:
                row = db.execute('SELECT data FROM hub_wishes WHERE id=?',(wish_id,)).fetchone()
                db.execute('DELETE FROM hub_wishes WHERE id=?',(wish_id,))
            # Deleted in the hub too, as the hub page deletes: a tombstone that every device follows.
            if row:self.hub.put_wish({**json.loads(row['data']),'deleted':True,'updatedAt':iso(time.time())})
            return {'ok':True}
        with self.lib.db() as db:
            db.execute('DELETE FROM wishlist WHERE id=?',(wish_id,))
        return {'ok':True}

    def thumb(self,wish_id):
        with self.lib.db() as db:
            row = db.execute(f'SELECT thumb FROM {self.table} WHERE id=?',(wish_id,)).fetchone()
        if not row or not row[0]:raise KeyError('没有海报。')
        return row[0]

    def search(self,query,kind=''):
        plex = self.lib.plex()
        return {'items':rank(search(plex.token,query,kind),query)}

    # The wish hub -------------------------------------------------------------------------------------------------

    def hub_item(self,values,item,now):
        """One wish in the hub's shape: movies as movie, series as tv; Plex's guid, poster and summary go along."""
        wish_id,title,year,kind,guid,thumb,summary,links,note = values
        item = dict(item or {'id':wish_id,'createdAt':iso(now),'done':False,'doneAt':None,'rating':3,'source':'plexus','aliases':[]})
        extra = {k:v for k,v in (item.get('extra') or {}).items() if k not in ('year','download')}
        if year:extra['year'] = str(year)
        ids = {k:v for k,v in (item.get('externalIds') or {}).items() if k!='plexGuid'}|({'plexGuid':guid} if guid else {})
        # The pasted share text stays whole: an extraction code is often on the line after its link.
        item.update(category='tv' if kind=='show' else 'movie',title=title,extra=extra,externalIds=ids,thumb=thumb,summary=summary,
                    links=[links] if links else [],note=note,updatedAt=iso(now))
        return item

    def save_to_hub(self,values):
        with self.lib.db() as db:
            row = db.execute('SELECT data FROM hub_wishes WHERE id=?',(values[0],)).fetchone()
        item = self.hub_item(values,json.loads(row['data']) if row else None,time.time())
        with self.lib.db() as db:self.store(db,item)
        self.hub.put_wish(item)
        self.report()

    def store(self,db,item):
        """Keep one of the hub's wishes here, or drop it when deleted."""
        if item.get('deleted') or item.get('category') not in ('movie','tv'):
            db.execute('DELETE FROM hub_wishes WHERE id=?',(item['id'],));return
        extra = item.get('extra') or {}
        year = str(extra.get('year') or '').strip()
        now = time.time()
        old = db.execute('SELECT rejected,reported FROM hub_wishes WHERE id=?',(item['id'],)).fetchone()
        links = [l for l in item.get('links') or [] if l]+([extra['download']] if extra.get('download') else [])
        db.execute('INSERT OR REPLACE INTO hub_wishes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (item['id'],str(item.get('title') or '未命名')[:200],int(year) if year.isdigit() else None,'show' if item['category']=='tv' else 'movie',
                    str((item.get('externalIds') or {}).get('plexGuid') or '')[:200],str(item.get('thumb') or '')[:500],str(item.get('summary') or '')[:600],
                    '\n'.join(links)[:5000],str(item.get('note') or '')[:1000],seconds(item.get('createdAt'),now),json.dumps(item,ensure_ascii=False),
                    old['rejected'] if old else '[]',old['reported'] if old else ''))

    def report(self):
        """Tell the hub which of its wishes Plex has now, or no longer has; each change once."""
        if not self.hub.paired:return 0
        rows = []
        with self.lib.db() as db:
            for wish in [dict(r) for r in db.execute('SELECT * FROM hub_wishes')]:
                found = self.owned(db,wish)
                ref = (found['guid'] or found['id']) if found else ''
                if ref==wish['reported']:continue
                if found:rows.append({'itemId':wish['id'],'ref':ref,'url':WEB+'#item='+urllib.parse.quote(found['id']),'title':found['title'],'ownedAt':iso(time.time())})
                # Gone from Plex: owned no more. A match the hub rejected is already not shown as owned there.
                elif wish['reported'] not in json.loads(wish['rejected']):rows.append({'itemId':wish['id'],'ref':wish['reported'],'ownedAt':None})
                db.execute('UPDATE hub_wishes SET reported=? WHERE id=?',(ref,wish['id']))
        # Queued while the hub cannot be reached, so a sync never waits on it.
        self.hub.fulfill(rows)
        return len(rows)

    def pull(self):
        """Bring in the hub's movie and series wishes changed since last time; False when not connected or offline."""
        if not self.hub.paired:return False
        with self.pulling:
            with self.lib.db() as db:
                row = db.execute("SELECT value FROM settings WHERE key='hub_cursor'").fetchone()
            try:
                self.hub.flush()
                data = self.hub.wishes(['movie','tv'],row[0] if row else None)
            except HubError:return False
            rejected = {}
            for f in data.get('fulfill') or []:
                if f.get('rejected'):rejected.setdefault(f['itemId'],[]).append(f['ref'])
            with self.lib.db() as db:
                for item in data.get('items') or []:
                    if isinstance(item,dict) and item.get('id'):self.store(db,item)
                db.execute("UPDATE hub_wishes SET rejected='[]'")
                for wish_id,refs in rejected.items():db.execute('UPDATE hub_wishes SET rejected=? WHERE id=?',(json.dumps(refs),wish_id))
                db.execute("INSERT OR REPLACE INTO settings VALUES ('hub_cursor',?)",(data['cursor'],))
            self.pulled = time.time()
        self.report()
        return True

    def refresh(self):
        """After a sync with Plex: the hub's latest wishes when it can be reached, and what Plex now has either way."""
        if not self.pull():self.report()

    def refresh_soon(self):
        """Opening the wishlist pulls in the background, so the page never waits on the hub."""
        if self.hub.paired and time.time()-self.pulled>PULL_SECONDS and not self.pulling.locked():
            self.pulled = time.time()
            threading.Thread(target=self.pull,daemon=True).start()

    def hub_status(self):
        with self.lib.db() as db:
            count = db.execute('SELECT COUNT(*) FROM hub_wishes').fetchone()[0]
        return self.hub.status()|{'wishes':count if self.hub.paired else 0}

    def pair(self,token):
        """Connect to the hub. The first time, this wishlist goes up, leaving out titles the hub already has."""
        try:self.hub.pair(token)
        except HubError as exc:raise ValueError(str(exc)) from None
        with self.lib.db() as db:
            db.execute('DELETE FROM hub_wishes');db.execute("DELETE FROM settings WHERE key='hub_cursor'")
            uploaded = db.execute("SELECT 1 FROM settings WHERE key='hub_uploaded'").fetchone()
        if not self.pull():raise ValueError('连接上了，但没能读取心愿中枢的想看清单，请稍后再试。')
        if not uploaded:
            now = time.time()
            with self.lib.db() as db:
                have = {(r['title'].casefold(),r['year']) for r in db.execute('SELECT title,year FROM hub_wishes')}
                have_guid = {r[0] for r in db.execute("SELECT guid FROM hub_wishes WHERE guid<>''")}
                local = [dict(r) for r in db.execute('SELECT * FROM wishlist ORDER BY added')]
            items = []
            for row in local:
                if (row['title'].casefold(),row['year']) in have or (row['guid'] and row['guid'] in have_guid):continue
                have.add((row['title'].casefold(),row['year']))
                values = tuple(row[k] for k in ('id','title','year','kind','guid','thumb','summary','links','note'))
                items.append(self.hub_item(values,None,now)|{'createdAt':iso(row['added'])})
            with self.lib.db() as db:
                for item in items:self.store(db,item)
                db.execute("INSERT OR REPLACE INTO settings VALUES ('hub_uploaded','1')")
            if items:self.hub.put_wish(items)
            self.report()
        return self.hub_status()

    def unpair(self):
        """Back to Plexus's own wishlist, as it was before connecting."""
        self.hub.unpair()
        with self.lib.db() as db:
            db.execute('DELETE FROM hub_wishes');db.execute("DELETE FROM settings WHERE key='hub_cursor'")
        return self.hub_status()
