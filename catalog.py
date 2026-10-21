"""One catalog of everything owned: what Plex lists, matched against the files actually on disk.

Every sync rebuilds the catalog from scratch; with a few thousand files that takes about a second."""
from __future__ import annotations

from collections import defaultdict
import hashlib
import ntpath
import os

from disk import drive, guess_title, norm, rank, walk

PAGE = 120
SORTS = {'added':'added DESC,title','title':'title COLLATE NOCASE,year','year':'year DESC,title','size':'size DESC,title'}
SCHEMA = '''
CREATE TABLE IF NOT EXISTS sections (id TEXT PRIMARY KEY,kind TEXT NOT NULL,title TEXT NOT NULL,locations TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS items (
  id TEXT PRIMARY KEY,source TEXT NOT NULL,section TEXT NOT NULL,kind TEXT NOT NULL,title TEXT NOT NULL,
  original_title TEXT NOT NULL,year INTEGER,summary TEXT NOT NULL,thumb TEXT NOT NULL,guid TEXT NOT NULL,
  added REAL NOT NULL,rating REAL,folder TEXT NOT NULL,
  size INTEGER NOT NULL,files INTEGER NOT NULL,episodes INTEGER NOT NULL,seasons INTEGER NOT NULL,
  resolution TEXT NOT NULL,drives TEXT NOT NULL,missing INTEGER NOT NULL,unindexed INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS media (
  id INTEGER PRIMARY KEY,item_id TEXT NOT NULL,episode_id TEXT NOT NULL,season INTEGER,episode INTEGER,
  episode_title TEXT NOT NULL,path TEXT NOT NULL,norm TEXT NOT NULL,drive TEXT NOT NULL,size INTEGER NOT NULL,
  resolution TEXT NOT NULL,width INTEGER,height INTEGER,video_codec TEXT NOT NULL,audio_codec TEXT NOT NULL,
  container TEXT NOT NULL,duration INTEGER,plex INTEGER NOT NULL,state TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS media_item ON media(item_id);
CREATE TABLE IF NOT EXISTS files (
  norm TEXT PRIMARY KEY,path TEXT NOT NULL,location TEXT NOT NULL,section TEXT NOT NULL,drive TEXT NOT NULL,
  size INTEGER NOT NULL,mtime REAL NOT NULL,kind TEXT NOT NULL,item_id TEXT NOT NULL,plex INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS files_item ON files(item_id);
'''
ITEM_FIELDS = 'id,source,section,kind,title,original_title,year,summary,thumb,guid,added,rating,folder'


def plex_item(meta,section):
    return {'id':'plex:'+str(meta['ratingKey']),'source':'plex','section':section['id'],'kind':section['kind'],
            'title':meta.get('title',''),'original_title':meta.get('originalTitle',''),'year':meta.get('year'),
            'summary':meta.get('summary',''),'thumb':meta.get('thumb',''),'guid':meta.get('guid',''),
            'added':float(meta.get('addedAt') or 0),'rating':meta.get('audienceRating') or meta.get('rating'),'folder':''}


def plex_media(meta,item_id,episode=None):
    """One row per file Plex plays for a movie or episode."""
    for version in meta.get('Media',[]):
        for part in version.get('Part',[]):
            if not part.get('file'):continue
            yield {'item_id':item_id,'episode_id':str(meta['ratingKey']) if episode else '',
                   'season':meta.get('parentIndex') if episode else None,'episode':meta.get('index') if episode else None,
                   'episode_title':meta.get('title','') if episode else '','path':part['file'],'norm':norm(part['file']),
                   'drive':drive(part['file']),'size':int(part.get('size') or 0),'resolution':str(version.get('videoResolution','')),
                   'width':version.get('width'),'height':version.get('height'),'video_codec':version.get('videoCodec',''),
                   'audio_codec':version.get('audioCodec',''),'container':part.get('container') or version.get('container',''),
                   'duration':part.get('duration') or version.get('duration'),'plex':1,'state':'ok'}


def disk_id(path):
    return 'disk:'+hashlib.sha1(norm(path).encode('utf-8')).hexdigest()[:16]


class Catalog:
    def __init__(self,lib):
        self.lib = lib
        with lib.db() as db:
            db.executescript(SCHEMA)

    def sync(self,plex,progress=lambda phase:None):
        progress('读取 Plex 媒体库')
        server = plex.server()
        sections = plex.sections()
        items,media = {},[]
        for section in sections:
            for meta in plex.items(section['id'],section['kind']):
                item = plex_item(meta,section)
                items[item['id']] = item
                if section['kind']=='movie':
                    media.extend(plex_media(meta,item['id']))
            if section['kind']=='show':
                for meta in plex.items(section['id'],'episode'):
                    item_id = 'plex:'+str(meta.get('grandparentRatingKey',''))
                    if item_id in items:media.extend(plex_media(meta,item_id,episode=True))
        progress('扫描硬盘')
        locations = []  # (normalized folder, folder, section), deepest first so nested folders win.
        for section in sections:
            for location in section['locations']:
                if os.path.isdir(location):locations.append((norm(location),location,section))
        locations.sort(key=lambda l:-len(l[0]))
        files = {}
        for key,location,section in locations:
            for path,size,mtime,kind in walk(location):
                files.setdefault(norm(path),{'norm':norm(path),'path':path,'location':location,'section':section['id'],'drive':drive(path),
                                             'size':size,'mtime':mtime,'kind':kind,'item_id':'','plex':0})
        progress('交叉对比')
        self._link(items,media,files,locations)
        with self.lib.db() as db:
            for table in ('sections','items','media','files'):db.execute(f'DELETE FROM {table}')
            db.executemany('INSERT INTO sections VALUES (?,?,?,?)',[(s['id'],s['kind'],s['title'],'\n'.join(s['locations'])) for s in sections])
            self._insert(db,'items',list(items.values()))
            self._insert(db,'media',media)
            self._insert(db,'files',list(files.values()))
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('plex_machine',server['machine']))
        return {'server':server,'items':len(items),'files':sum(f['kind']=='video' for f in files.values())}

    @staticmethod
    def _insert(db,table,rows):
        if rows:
            columns = list(rows[0])
            db.executemany(f'INSERT INTO {table} ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})',[[r[c] for c in columns] for r in rows])

    def _link(self,items,media,files,locations):
        """Mark which disk files Plex knows, gather the rest into their own entries, and total each entry."""
        def place(key):
            for root,location,section in locations:
                if key.startswith(root+'\\'):return root,location,section
            return None
        for row in media:
            found = files.get(row['norm'])
            if found:
                found['item_id'],found['plex'] = row['item_id'],1
            else:
                # Only library folders that are connected were scanned; a file elsewhere is not reported as lost.
                row['state'] = 'missing' if place(row['norm']) else 'offline'
        # Files Plex does not list join the Plex entry sharing their top folder, or become an entry of their own.
        owners = defaultdict(set)
        for f in files.values():
            if f['plex'] and f['kind']=='video':
                where = place(f['norm'])
                if where:owners[norm(self._group(f['path'],where[1]))].add(f['item_id'])
        for f in sorted(files.values(),key=lambda f:f['norm']):
            if f['plex'] or f['kind']!='video':continue
            root,location,section = place(f['norm'])
            group = self._group(f['path'],location)
            owner = owners.get(norm(group),set())
            if len(owner)==1:
                f['item_id'] = next(iter(owner))
            else:
                group = group if not owner else f['path']
                f['item_id'] = disk_id(group)
                if f['item_id'] not in items:
                    title,year = guess_title(ntpath.basename(group))
                    items[f['item_id']] = {'id':f['item_id'],'source':'disk','section':section['id'],'kind':section['kind'],
                                           'title':title,'original_title':'','year':year,'summary':'','thumb':'','guid':'',
                                           'added':f['mtime'],'rating':None,'folder':group}
                items[f['item_id']]['added'] = max(items[f['item_id']]['added'],f['mtime'])
            media.append({'item_id':f['item_id'],'episode_id':'','season':None,'episode':None,'episode_title':'',
                          'path':f['path'],'norm':f['norm'],'drive':f['drive'],'size':f['size'],'resolution':'','width':None,
                          'height':None,'video_codec':'','audio_codec':'','container':ntpath.splitext(f['path'])[1].lstrip('.').lower(),
                          'duration':None,'plex':0,'state':'ok'})
        # Subtitles belong to whatever entry owns a video in the same folder.
        folders = {ntpath.dirname(f['norm']):f['item_id'] for f in files.values() if f['item_id']}
        for f in files.values():
            if f['kind']=='subtitle':f['item_id'] = folders.get(ntpath.dirname(f['norm']),'')
        totals = defaultdict(lambda:{'size':0,'files':0,'episodes':set(),'seasons':set(),'resolution':'','drives':set(),'missing':0,'unindexed':0})
        for row in media:
            total = totals[row['item_id']]
            if row['state']=='missing':total['missing'] += 1
            if row['state']!='ok':continue
            total['size'] += row['size'];total['files'] += 1;total['drives'].add(row['drive'])
            total['unindexed'] += not row['plex']
            if row['episode_id']:total['episodes'].add(row['episode_id']);total['seasons'].add(row['season'])
            if rank(row['resolution'])<rank(total['resolution']):total['resolution'] = row['resolution']
        for item_id,item in items.items():
            total = totals[item_id]
            item.update(size=total['size'],files=total['files'],episodes=len(total['episodes']),seasons=len(total['seasons']),
                        resolution=total['resolution'],drives=','.join(sorted(total['drives'])),missing=total['missing'],unindexed=total['unindexed'])

    @staticmethod
    def _group(path,location):
        """The library folder's direct child that holds this file: a movie's or show's folder, or the file itself."""
        rest = ntpath.relpath(path,location)
        return ntpath.join(location,rest.split('\\')[0])

    def sections(self):
        with self.lib.db() as db:
            rows = db.execute('''SELECT s.id,s.kind,s.title,s.locations,COUNT(i.id) count FROM sections s
                                 LEFT JOIN items i ON i.section=s.id AND i.files>0 GROUP BY s.id ORDER BY CAST(s.id AS INTEGER)''').fetchall()
        return [dict(r)|{'locations':r['locations'].split('\n') if r['locations'] else []} for r in rows]

    def listing(self,query):
        where,args = ['(files>0 OR missing>0)'],[]
        if query.get('section'):where.append('section=?');args.append(query['section'])
        if query.get('source') in {'plex','disk'}:where.append('source=?');args.append(query['source'])
        if query.get('issue')=='unindexed':where.append('unindexed>0')
        if query.get('issue')=='missing':where.append('missing>0')
        if query.get('drive'):where.append("(','||drives||',') LIKE ?");args.append(f'%,{query["drive"]},%')
        if query.get('q'):
            like = f'%{query["q"].strip()}%'
            where.append('(title LIKE ? OR original_title LIKE ? OR folder LIKE ?)');args += [like,like,like]
        page = max(1,int(query.get('page') or 1))
        sql = ' AND '.join(where)
        with self.lib.db() as db:
            total = db.execute(f'SELECT COUNT(*) FROM items WHERE {sql}',args).fetchone()[0]
            rows = db.execute(f'''SELECT id,source,section,kind,title,original_title,year,thumb<>'' poster,added,size,files,episodes,seasons,
                                  resolution,drives,missing,unindexed FROM items WHERE {sql} ORDER BY {SORTS.get(query.get("sort"),SORTS["added"])}
                                  LIMIT ? OFFSET ?''',[*args,PAGE,(page-1)*PAGE]).fetchall()
        return {'items':[dict(r) for r in rows],'total':total,'page':page,'pages':max(1,-(-total//PAGE))}

    def item(self,item_id):
        with self.lib.db() as db:
            item = db.execute('SELECT * FROM items WHERE id=?',(item_id,)).fetchone()
            if not item:raise KeyError('这个条目已不在目录中，请重新同步。')
            media = [dict(r) for r in db.execute('SELECT * FROM media WHERE item_id=? ORDER BY season,episode,plex DESC,path',(item_id,))]
            subtitles = [dict(r) for r in db.execute("SELECT path,size FROM files WHERE item_id=? AND kind='subtitle' ORDER BY path",(item_id,))]
            section = db.execute('SELECT title FROM sections WHERE id=?',(item['section'],)).fetchone()
            machine = db.execute("SELECT value FROM settings WHERE key='plex_machine'").fetchone()
        for row in media:
            row.pop('norm')
        result = dict(item)|{'media':media,'subtitles':subtitles,'section_title':section[0] if section else ''}
        if item['source']=='plex' and machine:
            result['plex_url'] = f'https://app.plex.tv/desktop/#!/server/{machine[0]}/details?key=%2Flibrary%2Fmetadata%2F{item_id[5:]}'
        return result

    def thumb(self,item_id):
        with self.lib.db() as db:
            row = db.execute('SELECT thumb FROM items WHERE id=?',(item_id,)).fetchone()
        if not row or not row[0]:raise KeyError('没有海报。')
        return row[0]

    def stats(self):
        with self.lib.db() as db:
            row = db.execute('''SELECT COUNT(*) items,COALESCE(SUM(size),0) size,COALESCE(SUM(files),0) files,
                                COALESCE(SUM(source='disk'),0) disk_items,COALESCE(SUM(unindexed),0) unindexed,
                                COALESCE(SUM(missing),0) missing FROM items WHERE files>0 OR missing>0''').fetchone()
            drives = [dict(r) for r in db.execute("SELECT drive,COUNT(*) files,SUM(size) size FROM files WHERE kind='video' GROUP BY drive ORDER BY drive")]
        return dict(row)|{'drives':drives}
