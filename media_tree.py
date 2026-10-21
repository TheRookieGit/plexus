"""How many videos each drive and folder holds, and how many of them Plex knows, as a lazily browsed tree."""
import threading

from platform_utils import volume_label

FILE_LIMIT = 200


class MediaTree:
    def __init__(self,lib):
        self.lib = lib
        self.lock = threading.Lock()
        self.cache = None

    def invalidate(self):
        with self.lock:
            self.cache = None

    @staticmethod
    def _node():
        return {'children':{},'files':[],'total':0,'plex':0,'size':0}

    def _build(self):
        with self.lib.db() as db:
            rows = db.execute("SELECT norm,path,size,plex FROM files WHERE kind='video' ORDER BY path").fetchall()
        tree = self._node()
        for row in rows:
            parts = [p for p in row['path'].split('\\') if p]
            node = tree
            for depth,name in enumerate(parts):
                node['total'] += 1;node['plex'] += row['plex'];node['size'] += row['size']
                if depth==len(parts)-1:
                    node['files'].append(row['norm'])
                    break
                node = node['children'].setdefault(name,self._node())
        return tree

    def _tree(self):
        with self.lock:
            if self.cache is None:
                self.cache = self._build()
            return self.cache

    @staticmethod
    def _counts(node):
        return {'total':node['total'],'plex':node['plex'],'unindexed':node['total']-node['plex'],'size':node['size']}

    def node(self,path):
        if not isinstance(path,list) or not all(isinstance(p,str) for p in path):
            raise ValueError('无效的文件夹路径。')
        node = self._tree()
        for name in path:
            node = node['children'].get(name)
            if node is None:raise ValueError('这个文件夹已不在库中，请重新同步。')
        children = [{'name':name,'has_children':bool(child['children'] or child['files']),
                     'label':volume_label(name) if not path else '',**self._counts(child)} for name,child in node['children'].items()]
        children.sort(key=lambda c:c['name'] if not path else (-c['total'],c['name']))
        direct = node['files'][:FILE_LIMIT]
        files = []
        if direct:
            with self.lib.db() as db:
                found = {r['norm']:r for r in db.execute(f"SELECT norm,path,size,plex,item_id FROM files WHERE norm IN ({','.join('?' for _ in direct)})",direct)}
            files = [{'name':found[n]['path'].rsplit('\\',1)[-1],'size':found[n]['size'],'plex':found[n]['plex'],'item_id':found[n]['item_id']}
                     for n in direct if n in found]
        return {'path':path,'counts':self._counts(node),'children':children,'files':files,'direct_total':len(node['files'])}
