"""Plexus — a catalog of every movie and show owned, kept in step with the local Plex Media Server."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import sqlite3
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler

# Code shared with 书屿 and the wish hub: the my-collections repository, kept as a submodule.
sys.path.insert(0,str(Path(__file__).resolve().parent/'vendor'/'my-collections'))

from app_runtime import bind_server, runtime_identity
from catalog import Catalog
from deleter import Deleter
from disk import norm
from downloads import Downloads
from netdisk import NetDisk, NetDiskError
from wishlist import Wishlist
from media_tree import MediaTree
from plex import DEFAULT_URL, Plex, PlexError
from platform_utils import reveal_in_explorer
from remote_access import RemoteAccess, SESSION_DAYS, LOCAL_HOSTS, is_loopback, lan_addresses

BASE = Path(__file__).resolve().parent
PORT = 8766
SYNC_SECONDS = 300
POSTER_HOSTS = {'image.tmdb.org','metadata-static.plex.tv','images.plex.tv','provider-static.plex.tv','m.media-amazon.com','artworks.thetvdb.com'}


class UserError(Exception):
    pass


class Library:
    def __init__(self,data_dir,interval=SYNC_SECONDS):
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True,exist_ok=True)
        (self.data_dir/'posters').mkdir(exist_ok=True)
        self.db_path = self.data_dir/'library.sqlite3'
        self.interval = interval
        self.sync_lock = threading.Lock()
        self.wake = threading.Event()
        self.sync_state = {'running':False,'phase':'','error':'','last_sync':None,'server':None}
        self.deleted = {}  # Recently deleted paths, so the catalog does not call them lost before Plex catches up.
        with self.db() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY,value TEXT NOT NULL);
            ''')
        self.catalog = Catalog(self)
        self.tree = MediaTree(self)
        self.access = RemoteAccess(self)
        self.deleter = Deleter(self)
        self.wishlist = Wishlist(self)
        self.netdisk = NetDisk(self)
        self.downloads = Downloads(self)

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.db_path,timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def setting(self,key,default=''):
        with self.db() as db:
            row = db.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
        return row[0] if row else default

    def forget_paths(self,paths):
        now = time.time()
        self.deleted = {k:v for k,v in self.deleted.items() if v>now}|{norm(p):now+1800 for p in paths}
        with self.db() as db:
            gone = [(row[0],) for row in db.execute('SELECT norm FROM files') if self.recently_deleted(row[0])]
            db.executemany('DELETE FROM files WHERE norm=?',gone)
        self.tree.invalidate()

    def recently_deleted(self,key):
        now = time.time()
        return any(v>now and (key==k or key.startswith(k+'\\')) for k,v in self.deleted.items())

    def plex(self):
        """Tests replace this."""
        return Plex(self.setting('plex_url',DEFAULT_URL))

    def start_sync(self):
        if not self.sync_lock.acquire(blocking=False):
            return False
        self.sync_state.update(running=True,phase='连接 Plex',error='')
        threading.Thread(target=self._sync,daemon=True,name='plex-sync').start()
        return True

    def _sync(self):
        try:
            result = self.catalog.sync(self.plex(),lambda phase:self.sync_state.update(phase=phase))
            self.tree.invalidate()
            self.sync_state.update(server=result['server'],last_sync=time.time())
        except PlexError as exc:
            self.sync_state['error'] = str(exc)
        except Exception as exc:  # A failed sync must not stop the next one.
            self.sync_state['error'] = f'同步失败：{exc}'
        finally:
            self.sync_state.update(running=False,phase='')
            self.sync_lock.release()

    def auto_sync(self):
        while True:
            self.wake.wait(self.interval)
            self.wake.clear()
            self.start_sync()

    def status(self):
        return {'sync':dict(self.sync_state),'stats':self.catalog.stats(),'sections':self.catalog.sections(),'downloads':self.downloads.summary(),
                'plex_url':self.setting('plex_url',DEFAULT_URL)}

    def poster(self,item_id,width,url=''):
        """A poster resized by Plex and cached here so later pages load without asking Plex again.
        Wishlist entries and search results have posters from Plex's online catalog, which Plex's transcoder also fetches."""
        width = 600 if width>300 else 300
        if url:
            if urllib.parse.urlsplit(url).hostname not in POSTER_HOSTS:raise UserError('不支持的海报地址。')
            thumb = url
        else:
            thumb = self.wishlist.thumb(item_id[5:]) if item_id.startswith('wish:') else self.catalog.thumb(item_id)
        cached = self.data_dir/'posters'/f'{hashlib.sha1(f"{thumb}|{width}".encode()).hexdigest()}.jpg'
        if not cached.exists():
            data = self.plex().image(thumb,width,width*3//2)
            temporary = cached.with_suffix('.part')
            temporary.write_bytes(data)
            os.replace(temporary,cached)
        return cached.read_bytes()

    def reveal(self,path):
        """Show a file of the catalog in Explorer; only paths the catalog knows may be opened."""
        with self.db() as db:
            known = db.execute('SELECT 1 FROM files WHERE path=?',(path,)).fetchone()
        if not known:raise UserError('这个文件已不在库中，请重新同步。')
        reveal_in_explorer(path)
        return {'ok':True}

    def settings(self,data):
        url = str(data.get('plex_url','')).strip().rstrip('/')
        if url and not url.startswith(('http://','https://')):raise UserError('Plex 地址需要以 http:// 或 https:// 开头。')
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('plex_url',url or DEFAULT_URL))
        self.start_sync()
        return {'ok':True}


class Handler(BaseHTTPRequestHandler):
    server_version = 'Plexus/0.1'
    STATIC = {'/':'web/index.html',**{f'/{name}':f'web/{name}' for name in ('downloads.html','common.js','app.js','tree.js','downloads.js','style.css','favicon.svg','apple-touch-icon.png')}}
    PUBLIC_API = {'/api/session','/api/login'}
    # These act on the Windows machine itself, so other computers may not call them.
    LOCAL_ONLY = {'/api/runtime/reload','/api/app/show','/api/reveal','/api/access/password','/api/settings'}
    FORWARDED = ('X-Forwarded-For','Forwarded','Cf-Connecting-Ip','X-Real-Ip','Tailscale-User-Login')

    @property
    def lib(self):
        return self.server.library

    def log_message(self,*args):
        pass

    def same_origin(self):
        origin = self.headers.get('Origin')
        return not origin or urllib.parse.urlsplit(origin).netloc==self.headers.get('Host','')

    def allowed_origin(self):
        origin = self.headers.get('Origin','')
        return origin if origin and origin in getattr(self.server,'allowed_origins',()) else ''

    def is_local(self):
        # Tunnels connect from loopback too, so a proxied or cross-site request is never trusted as local.
        return (is_loopback(self.client_address[0]) and self.same_origin() and not any(self.headers.get(h) for h in self.FORWARDED)
                and self.headers.get('Host','') in {f'{h}:{self.server.server_port}' for h in LOCAL_HOSTS})

    def client_ip(self):
        # Behind a local tunnel every visitor arrives from loopback; the tunnel names the real address.
        forwarded = self.headers.get('Cf-Connecting-Ip') or self.headers.get('X-Forwarded-For','').split(',')[0].strip()
        return forwarded if forwarded and is_loopback(self.client_address[0]) else self.client_address[0]

    def token(self):
        scheme,_,value = self.headers.get('Authorization','').partition(' ')
        return value.strip() if scheme.lower()=='bearer' else ''

    def authorized(self):
        return self.is_local() or self.lib.access.valid(self.token())

    def guard(self,path):
        """Send a refusal and return False when this request may not reach the route."""
        if not self.same_origin() and not self.allowed_origin():
            self.json({'error':'不允许跨站请求。'},403);return False
        if path in self.LOCAL_ONLY and not self.is_local():
            self.json({'error':'此操作只能在运行 Plex 的 Windows 电脑上进行。'},403);return False
        if path.startswith('/api/') and path not in self.PUBLIC_API and not self.authorized():
            self.json({'error':'请先登录。','login':True},401);return False
        return True

    def end_headers(self):
        origin = self.allowed_origin()
        if origin:
            self.send_header('Access-Control-Allow-Origin',origin)
            self.send_header('Vary','Origin')
        super().end_headers()

    def do_OPTIONS(self):
        if not self.allowed_origin():
            return self.json({'error':'不允许跨站请求。'},403)
        self.send_response(204)
        self.send_header('Access-Control-Allow-Methods','GET, POST')
        self.send_header('Access-Control-Allow-Headers','Authorization, Content-Type')
        self.send_header('Access-Control-Max-Age','600')
        self.send_header('Content-Length','0')
        self.end_headers()

    def send_content(self,status,content_type,payload,extra=None):
        headers = {'Content-Type':content_type,'Content-Length':str(len(payload)),'X-Content-Type-Options':'nosniff',
                   'Referrer-Policy':'no-referrer','Cache-Control':'no-store'}|(extra or {})
        self.send_response(status)
        for key,value in headers.items():
            self.send_header(key,value)
        self.end_headers()
        self.wfile.write(payload)

    def json(self,data,status=200,extra=None):
        self.send_content(status,'application/json; charset=utf-8',json.dumps(data,ensure_ascii=False).encode(),extra)

    def session(self):
        local = self.is_local()
        info = {'local':local,'authenticated':bool(self.authorized()),'password_set':self.lib.access.password_set}
        if local:
            info['origins'] = sorted(getattr(self.server,'allowed_origins',()))
            if getattr(self.server,'lan',False):
                info['addresses'] = [f'http://{a}:{self.server.server_port}' for a in lan_addresses()]
        return info

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        if not self.guard(url.path):return
        query = {k:v[-1] for k,v in urllib.parse.parse_qs(url.query).items()}
        try:
            if url.path=='/api/runtime' and hasattr(self.server,'runtime'):
                return self.json(self.server.runtime)
            if url.path=='/api/session':return self.json(self.session())
            if url.path=='/api/status':return self.json(self.lib.status())
            if url.path=='/api/items':return self.json(self.lib.catalog.listing(query))
            if url.path=='/api/item':return self.json(self.lib.catalog.item(query['id']))
            if url.path=='/api/deletions':return self.json({'items':self.lib.deleter.history()})
            if url.path=='/api/tree':return self.json(self.lib.tree.node(json.loads(query.get('path','[]'))))
            if url.path=='/api/poster':
                return self.send_content(200,'image/jpeg',self.lib.poster(query.get('id',''),int(query.get('w') or 300),query.get('url','')),{'Cache-Control':'private, max-age=86400'})
            if url.path=='/api/downloads':return self.json(self.lib.downloads.listing())
            if url.path=='/api/netdisk':return self.json(self.lib.netdisk.status())
            if url.path=='/api/wishlist':return self.json(self.lib.wishlist.items())
            if url.path=='/api/search':return self.json(self.lib.wishlist.search(query.get('q',''),query.get('kind','')))
            if url.path in self.STATIC:
                path = BASE/self.STATIC[url.path]
                kind = mimetypes.guess_type(path.name)[0] or 'text/plain'
                return self.send_content(200,kind if kind.startswith('image/') and kind!='image/svg+xml' else kind+'; charset=utf-8',path.read_bytes(),
                    {'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; frame-src 'none'; object-src 'none'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'"})
            return self.json({'error':'Not found'},404)
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
        except PlexError as exc:self.json({'error':str(exc)},502)
        except NetDiskError as exc:self.json({'error':str(exc),'netdisk_login':exc.login},400)
        except (UserError,OSError,ValueError,KeyError) as exc:self.json({'error':str(exc).strip("'")},400)

    def do_POST(self):
        if not self.guard(self.path):return
        if self.headers.get('Content-Type','').split(';')[0]!='application/json':
            return self.json({'error':'Expected JSON'},415)
        try:
            length = int(self.headers.get('Content-Length','0'))
            if not 0<length<=1024*1024:raise UserError('无效的请求长度。')
            data = json.loads(self.rfile.read(length))
            if self.path=='/api/login':
                try:token = self.lib.access.login(data.get('password'),self.client_ip())
                except PermissionError as exc:return self.json({'error':str(exc)},403)
                return self.json({'ok':True,'token':token,'days':SESSION_DAYS})
            if self.path=='/api/logout':
                self.lib.access.logout(self.token())
                return self.json({'ok':True})
            if self.path=='/api/access/password':
                self.lib.access.set_password(data.get('password'))
                return self.json({'ok':True})
            if self.path=='/api/app/show':
                if not callable(getattr(self.server,'on_show',None)):return self.json({'ok':False})
                self.server.on_show()
                return self.json({'ok':True})
            if self.path=='/api/runtime/reload' and hasattr(self.server,'runtime'):
                if data.get('instance')!=self.server.runtime['instance']:
                    return self.json({'error':'后台服务已变化，请重新启动。'},409)
                self.json({'ok':True})
                threading.Thread(target=self.server.shutdown,daemon=True).start()
                return
            if self.path=='/api/delete/preview':return self.json(self.lib.deleter.preview(data))
            if self.path=='/api/delete/confirm':return self.json(self.lib.deleter.confirm(data.get('token')))
            # Quark and Baidu share links: sign in by QR code once, then queue downloads into a library folder.
            if self.path=='/api/netdisk/login':return self.json(self.lib.netdisk.drive(str(data.get('drive',''))).login())
            if self.path=='/api/netdisk/logout':return self.json(self.lib.netdisk.drive(str(data.get('drive',''))).logout())
            if self.path=='/api/downloads/resolve':return self.json(self.lib.downloads.resolve(str(data.get('text',''))[:20000]))
            if self.path=='/api/downloads/add':return self.json(self.lib.downloads.add(data.get('token'),data.get('files') or [],str(data.get('location',''))))
            if self.path=='/api/downloads/control':return self.json(self.lib.downloads.control(data))
            if self.path=='/api/downloads/identify':return self.json(self.lib.downloads.identify(data))
            if self.path=='/api/downloads/apply':return self.json(self.lib.downloads.apply(str(data.get('id','')),bool(data.get('rename',True))))
            if self.path=='/api/favorite':return self.json(self.lib.wishlist.favorite(str(data['id']),bool(data.get('on'))))
            if self.path=='/api/wishlist/save':return self.json(self.lib.wishlist.save(data))
            if self.path=='/api/wishlist/delete':return self.json(self.lib.wishlist.delete(data.get('id')))
            if self.path=='/api/sync':return self.json({'started':self.lib.start_sync()})
            if self.path=='/api/settings':return self.json(self.lib.settings(data))
            if self.path=='/api/reveal':return self.json(self.lib.reveal(str(data.get('path',''))))
            return self.json({'error':'Not found'},404)
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
        except PlexError as exc:self.json({'error':str(exc)},502)
        except NetDiskError as exc:self.json({'error':str(exc),'netdisk_login':exc.login},400)
        except (UserError,OSError,ValueError,KeyError,TypeError) as exc:self.json({'error':str(exc).strip("'")},400)


def default_data_dir():
    # The packaged app keeps data per Windows user, so replacing the app folder keeps the catalog.
    if getattr(sys,'frozen',False):
        return Path(os.environ.get('LOCALAPPDATA') or Path.home())/'Plexus'
    return BASE/'.data'


def build_parser():
    parser = argparse.ArgumentParser(description='Plexus · 和 Plex 同步的影视收藏')
    parser.add_argument('--port',type=int,default=PORT)
    parser.add_argument('--data-dir',default=str(default_data_dir()))
    parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--lan',action='store_true',help='同时监听局域网，其他电脑可直接用 IP 地址登录访问')
    parser.add_argument('--allow-origin',action='append',default=[],help='允许调用后台的网页来源，例如 https://用户名.github.io（可重复）')
    return parser


def start(args):
    """Bind and load the catalog; None when a matching instance already serves this port."""
    identity = runtime_identity(BASE,args.data_dir,'0.0.0.0' if args.lan else '127.0.0.1')
    server = bind_server(args.port,Handler,identity)
    if server is None:
        return None
    try:
        lib = Library(args.data_dir)
    except BaseException:
        server.server_close()
        raise
    server.library = lib
    server.lan = args.lan
    server.allowed_origins = {o.rstrip('/') for o in args.allow_origin}
    lib.start_sync()
    lib.downloads.start()
    threading.Thread(target=lib.auto_sync,daemon=True).start()
    return server


def main():
    args = build_parser().parse_args()
    server = start(args)
    url = f'http://127.0.0.1:{args.port}'
    if server is None:
        if not args.no_browser:webbrowser.open(url)
        return
    print(f'Plexus 已启动：{url}\n每 {SYNC_SECONDS//60} 分钟自动和 Plex 同步一次。关闭此窗口即可停止。',flush=True)
    if server.allowed_origins:print('允许网页版访问：'+'，'.join(sorted(server.allowed_origins)),flush=True)
    if args.lan:
        addresses = '\n'.join(f'  http://{a}:{args.port}' for a in lan_addresses()) or '  （未检测到局域网地址）'
        print(f'局域网地址：\n{addresses}',flush=True)
    if not args.no_browser:threading.Timer(.6,lambda:webbrowser.open(url)).start()
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()


if __name__=='__main__':main()
