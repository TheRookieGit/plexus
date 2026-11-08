"""Favourites among what is owned, and a wishlist of titles still to get, each with the share links to get it from.

Connected to the wish hub (心愿中枢, the my-collections site), the wishlist is the hub's movies and series instead:
kept here in hub_wishes, owned as before by Plex's guid or by title and year, and each title Plex has is reported
back so the hub shows it as owned, and the hub's original screenshots of a wish can be seen, added and removed
here. A match the hub marks 「不是这个」 is passed over. The wishlist kept here before
connecting goes up to the hub once and stays as it was, for going back. Favourites stay here.

A wish not yet matched to a title in Plex's online catalog, added here without picking one or brought in from the hub,
is matched in the background when its title is clear enough, so it gets Plex's poster, and the match goes up to the hub.

A wish's share links go both ways, one entry for each link: added or removed on either side, the other follows, and
when both change at once neither change is lost."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re
import threading
import time
import urllib.parse
import uuid

from collectionkit.hub import Hub, HubError
from collectionkit.netdisk import links as share_links
from discover import rank, search
from plex import PlexError

WEB = 'https://therookiegit.github.io/plexus/'  # Where the hub's 「查看」 opens an owned title.
PULL_SECONDS = 60
LINK = re.compile(r'(?:https?://|magnet:)',re.I)
# A wish is owned once the catalog has files of the same Plex title, or of the same title within a year.
OWNED = '''SELECT id,title,guid FROM items WHERE files>0 AND ((?<>'' AND guid=?) OR (title=? AND (? IS NULL OR year IS NULL OR ABS(year-?)<=1)))
           ORDER BY source='plex' DESC LIMIT 20'''


def split_links(text):
    """The pasted text as one entry for each link. A line with no link, such as an extraction code, stays with the link above it."""
    entries = []
    for line in str(text or '').splitlines():
        line = line.strip()
        if not line:continue
        if not entries or LINK.search(line):entries.append(line)
        else:entries[-1] += '\n'+line
    return entries


def link_key(entry):
    """The same share however it is written: with its code in the address or after it, say."""
    found = share_links(entry)
    return f'{found[0]["drive"]}:{found[0]["id"]}' if found else entry.strip()


def merge_links(mine,theirs,last):
    """Three-way: last is what the two sides agreed on. What either side added stays, what either side removed goes."""
    here,agreed = {link_key(e) for e in mine},{link_key(e) for e in last}
    kept = [e for e in theirs if link_key(e) in here or link_key(e) not in agreed]+[e for e in mine if link_key(e) not in agreed]
    return list({link_key(e):e for e in reversed(kept)}.values())[::-1]


def plain(text):
    """A title without spaces or punctuation, for comparing: 「第二期·发光的蝴蝶（毛孩子TV)」 and 「第二期 发光的蝴蝶 (毛孩子TV）」 are the same."""
    return re.sub(r'[\W_]+','',str(text or '')).casefold()


def best_match(results,title,year=None):
    """The catalog title a wish means, or None when it is not clear: one of the same name and year; or, for a wish with a year,
    Plex's own first result when its year agrees, as Plex finds 「Noelle」 under its Chinese title 诺艾尔 without the original."""
    key = plain(title)
    if not key or not results:return None
    def near(item):return bool(year and item.get('year') and abs(item['year']-year)<=1)
    for item in rank(results,title,year):
        if key in {plain(item['title']),plain(item.get('original_title'))} and (not year or not item.get('year') or near(item)):return item
    return results[0] if near(results[0]) else None


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
        self.pull_thread = None
        self.matching = threading.Lock()
        self.match_wanted = False
        with lib.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS favorites (key TEXT PRIMARY KEY,added REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS wishlist (id TEXT PRIMARY KEY,title TEXT NOT NULL,year INTEGER,kind TEXT NOT NULL,
              guid TEXT NOT NULL,thumb TEXT NOT NULL,summary TEXT NOT NULL,links TEXT NOT NULL,note TEXT NOT NULL,added REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS hub_wishes (id TEXT PRIMARY KEY,title TEXT NOT NULL,year INTEGER,kind TEXT NOT NULL,
              guid TEXT NOT NULL,thumb TEXT NOT NULL,summary TEXT NOT NULL,links TEXT NOT NULL,note TEXT NOT NULL,added REAL NOT NULL,
              data TEXT NOT NULL,rejected TEXT NOT NULL DEFAULT '[]',reported TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS wish_match_tried (id TEXT PRIMARY KEY,key TEXT NOT NULL);
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
                row['shots'] = self.shots_of(row)
                for key in ('data','rejected','reported'):row.pop(key,None)
        self.match_soon()
        return {'items':rows,'hub':self.hub.paired,'pending':self.busy()}

    def save(self,data):
        title = str(data.get('title','')).strip()[:200]
        if not title:raise ValueError('请填写片名。')
        year = int(data['year']) if str(data.get('year') or '').strip().isdigit() else None
        kind = data.get('kind') if data.get('kind') in {'movie','show'} else 'movie'
        wish_id = str(data.get('id') or '') or uuid.uuid4().hex[:12]
        values = (wish_id,title,year,kind,str(data.get('guid',''))[:200],str(data.get('thumb',''))[:500],str(data.get('summary',''))[:600],
                  str(data.get('links','')).strip()[:5000],str(data.get('note','')).strip()[:1000])
        if self.hub.paired:self.save_to_hub(values,data.get('shots'))
        else:
            with self.lib.db() as db:
                added = db.execute('SELECT added FROM wishlist WHERE id=?',(wish_id,)).fetchone()
                db.execute('INSERT OR REPLACE INTO wishlist VALUES (?,?,?,?,?,?,?,?,?,?)',(*values,added[0] if added else time.time()))
        if not values[4]:self.match_soon()
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

    # Matching wishes to Plex's online catalog ---------------------------------------------------------------------

    def find(self,title,kind):
        """Tests replace this."""
        return search(self.lib.plex().token,title,kind)

    def match(self):
        """Match each wish without a Plex title, once for each title it is given; the number matched.
        While the catalog cannot be searched (offline, Plex not set up) the rest wait for next time."""
        table = self.table
        with self.lib.db() as db:
            tried = dict(db.execute('SELECT id,key FROM wish_match_tried').fetchall())
            wishes = [dict(r) for r in db.execute(f"SELECT id,title,year,kind FROM {table} WHERE guid='' ORDER BY added DESC")]
        matched,changed = 0,[]
        for wish in wishes:
            key = json.dumps([wish['title'],wish['year'],wish['kind']],ensure_ascii=False)
            if tried.get(wish['id'])==key:continue
            try:found = best_match(self.find(wish['title'],wish['kind']),wish['title'],wish['year'])
            except (PlexError,OSError,ValueError):break
            with self.lib.db() as db:
                db.execute('INSERT OR REPLACE INTO wish_match_tried VALUES (?,?)',(wish['id'],key))
                if not found or not found.get('guid'):continue
                # Only while the wish is as it was when searched: an edit made meanwhile wins.
                row = db.execute(f"SELECT * FROM {table} WHERE id=? AND guid='' AND title=?",(wish['id'],wish['title'])).fetchone()
                if not row:continue
                matched += 1
                if table=='wishlist':
                    db.execute('UPDATE wishlist SET guid=?,thumb=?,summary=?,year=? WHERE id=?',
                               (found['guid'][:200],row['thumb'] or str(found.get('thumb') or '')[:500],row['summary'] or str(found.get('summary') or '')[:600],
                                row['year'] or found.get('year'),wish['id']))
                    continue
                item = json.loads(row['data'])
                item['externalIds'] = (item.get('externalIds') or {})|{'plexGuid':found['guid']}
                if not item.get('thumb') and found.get('thumb'):item['thumb'] = found['thumb']
                if not item.get('summary') and found.get('summary'):item['summary'] = found['summary']
                if not row['year'] and found.get('year'):item['extra'] = (item.get('extra') or {})|{'year':str(found['year'])}
                item['updatedAt'] = iso(time.time())
                self.store(db,item)
                changed.append(item)
        # Queued while the hub cannot be reached.
        if changed:self.hub.put_wish(changed)
        if matched:self.report()
        return matched

    def match_soon(self):
        """Match in the background, so adding or syncing never waits on Plex's online catalog."""
        self.match_wanted = True
        if not self.matching.acquire(blocking=False):return
        def run():
            try:
                while self.match_wanted:
                    self.match_wanted = False
                    try:self.match()
                    except Exception:pass  # Tried again on the next add, sync or visit.
            finally:self.matching.release()
        threading.Thread(target=run,daemon=True,name='wish-match').start()

    def busy(self):
        """Still pulling from the hub or matching, so the page looks again shortly."""
        return self.matching.locked() or self.match_wanted or bool(self.pull_thread and self.pull_thread.is_alive())

    # The wish hub -------------------------------------------------------------------------------------------------

    @staticmethod
    def shots_of(row):
        try:shots = json.loads(row.get('data') or '{}').get('shots')
        except ValueError:shots = None
        return [s for s in shots if isinstance(s,str)] if isinstance(shots,list) else []

    def shot(self,shot_id):
        """One of the hub's screenshots as a data URL; fetched from the hub the first time."""
        try:data = self.hub.shot(str(shot_id or ''))
        except HubError as exc:raise ValueError(str(exc)) from None
        if not data:raise ValueError('心愿中枢里没有这张截图了。')
        return {'data':data}

    def add_shot(self,data):
        """Keep a screenshot for a wish; its id goes in the wish's shots when the wish is saved."""
        if not self.hub.paired:raise ValueError('连接心愿中枢后才能保存截图。')
        try:return {'id':self.hub.add_shot(data)}
        except HubError as exc:raise ValueError(str(exc)) from None

    def hub_item(self,values,item,now):
        """One wish in the hub's shape: movies as movie, series as tv; Plex's guid, poster and summary go along."""
        wish_id,title,year,kind,guid,thumb,summary,links,note = values
        item = dict(item or {'id':wish_id,'createdAt':iso(now),'done':False,'doneAt':None,'rating':3,'source':'plexus','aliases':[]})
        extra = {k:v for k,v in (item.get('extra') or {}).items() if k not in ('year','download')}
        if year:extra['year'] = str(year)
        ids = {k:v for k,v in (item.get('externalIds') or {}).items() if k!='plexGuid'}|({'plexGuid':guid} if guid else {})
        # An extraction code is often on the line after its link, and stays with it.
        item.update(category='tv' if kind=='show' else 'movie',title=title,extra=extra,externalIds=ids,thumb=thumb,summary=summary,
                    links=split_links(links),note=note,updatedAt=iso(now))
        return item

    def links_of(self,wish_id):
        with self.lib.db() as db:
            row = db.execute('SELECT links FROM hub_wishes WHERE id=?',(wish_id,)).fetchone()
        return split_links(row[0]) if row else []

    def save_to_hub(self,values,shots=None):
        # Links may have changed in the hub while this wish was open here: keep both sides' changes. Offline there is nothing newer to see.
        agreed = self.links_of(values[0])
        self.pull()
        with self.lib.db() as db:
            row = db.execute('SELECT data FROM hub_wishes WHERE id=?',(values[0],)).fetchone()
        merged = merge_links(split_links(values[7]),self.links_of(values[0]),agreed)
        values = (*values[:7],'\n'.join(merged)[:5000],values[8])
        item = self.hub_item(values,json.loads(row['data']) if row else None,time.time())
        if isinstance(shots,list):item['shots'] = [str(s)[:64] for s in shots if isinstance(s,str) and s][:30]
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
        self.match_soon()
        return True

    def refresh(self):
        """After a sync with Plex: the hub's latest wishes when it can be reached, and what Plex now has either way."""
        if not self.pull():self.report()

    def refresh_soon(self):
        """Opening the wishlist pulls in the background, so the page never waits on the hub."""
        if self.hub.paired and time.time()-self.pulled>PULL_SECONDS and not self.pulling.locked():
            self.pulled = time.time()
            self.pull_thread = threading.Thread(target=self.pull,daemon=True)
            self.pull_thread.start()

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
