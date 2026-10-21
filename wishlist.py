"""Favourites among what is owned, and a wishlist of titles still to get, each with the share links to download it from."""
from __future__ import annotations

import time
import uuid

from naming import rank, search


class Wishlist:
    def __init__(self,lib):
        self.lib = lib
        with lib.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS favorites (key TEXT PRIMARY KEY,added REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS wishlist (id TEXT PRIMARY KEY,title TEXT NOT NULL,year INTEGER,kind TEXT NOT NULL,
              guid TEXT NOT NULL,thumb TEXT NOT NULL,summary TEXT NOT NULL,links TEXT NOT NULL,note TEXT NOT NULL,added REAL NOT NULL);
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

    def items(self):
        with self.lib.db() as db:
            rows = [dict(r) for r in db.execute('SELECT * FROM wishlist ORDER BY added DESC')]
            for row in rows:
                owned = db.execute('''SELECT id FROM items WHERE files>0 AND ((?<>'' AND guid=?) OR (title=? AND (? IS NULL OR year IS NULL OR ABS(year-?)<=1)))
                                      ORDER BY source='plex' DESC LIMIT 1''',(row['guid'],row['guid'],row['title'],row['year'],row['year'])).fetchone()
                row['owned'] = owned[0] if owned else ''
        return {'items':rows}

    def save(self,data):
        title = str(data.get('title','')).strip()[:200]
        if not title:raise ValueError('请填写片名。')
        year = int(data['year']) if str(data.get('year') or '').strip().isdigit() else None
        kind = data.get('kind') if data.get('kind') in {'movie','show'} else 'movie'
        wish_id = str(data.get('id') or '') or uuid.uuid4().hex[:12]
        values = (wish_id,title,year,kind,str(data.get('guid',''))[:200],str(data.get('thumb',''))[:500],str(data.get('summary',''))[:600],
                  str(data.get('links','')).strip()[:5000],str(data.get('note','')).strip()[:1000])
        with self.lib.db() as db:
            added = db.execute('SELECT added FROM wishlist WHERE id=?',(wish_id,)).fetchone()
            db.execute('INSERT OR REPLACE INTO wishlist VALUES (?,?,?,?,?,?,?,?,?,?)',(*values,added[0] if added else time.time()))
        return {'id':wish_id}

    def delete(self,wish_id):
        with self.lib.db() as db:
            db.execute('DELETE FROM wishlist WHERE id=?',(str(wish_id),))
        return {'ok':True}

    def thumb(self,wish_id):
        with self.lib.db() as db:
            row = db.execute('SELECT thumb FROM wishlist WHERE id=?',(wish_id,)).fetchone()
        if not row or not row[0]:raise KeyError('没有海报。')
        return row[0]

    def search(self,query,kind=''):
        plex = self.lib.plex()
        return {'items':rank(search(plex.token,query,kind),query)}
