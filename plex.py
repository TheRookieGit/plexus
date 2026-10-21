"""Read the local Plex Media Server: its libraries, every movie, show and episode, and the files behind them.

Plexus only reads from Plex, apart from asking it to rescan a folder after a download and to tidy up after a delete."""
from __future__ import annotations

import json
import os
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

DEFAULT_URL = 'http://127.0.0.1:32400'
PAGE = 1000
TYPES = {'movie':1,'show':2,'episode':4}


class PlexError(Exception):
    pass


def local_token():
    """The token of the Plex Media Server signed in on this Windows account, or ''."""
    if os.name=='nt':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r'Software\Plex, Inc.\Plex Media Server') as key:
                return str(winreg.QueryValueEx(key,'PlexOnlineToken')[0])
        except OSError:
            pass
    try:
        return ET.parse(Path(os.environ.get('LOCALAPPDATA',''))/'Plex Media Server'/'Preferences.xml').getroot().get('PlexOnlineToken','')
    except (OSError,ET.ParseError):
        return ''


def fetch(request,timeout):
    """(status, content type, body) for one exchange. Tests replace this."""
    try:response = urllib.request.urlopen(request,timeout=timeout)
    except urllib.error.HTTPError as exc:response = exc
    except (urllib.error.URLError,TimeoutError,OSError) as exc:
        raise PlexError('连不上 Plex Media Server，请确认它在这台电脑上运行。') from exc
    with response:
        return response.status or response.code,response.headers.get('Content-Type',''),response.read()


class Plex:
    def __init__(self,url=DEFAULT_URL,token=None):
        self.url = (url or DEFAULT_URL).rstrip('/')
        self.token = local_token() if token is None else token

    def call(self,path,params=None,method='GET',start=None,accept='application/json',timeout=30):
        if not self.token:
            raise PlexError('没有找到 Plex 的登录信息，请确认这台电脑上的 Plex Media Server 已登录。')
        query = f'?{urllib.parse.urlencode(params)}' if params else ''
        headers = {'X-Plex-Token':self.token,'Accept':accept,'X-Plex-Product':'Plexus','X-Plex-Client-Identifier':'plexus'}
        if start is not None:
            headers |= {'X-Plex-Container-Start':str(start),'X-Plex-Container-Size':str(PAGE)}
        status,kind,body = fetch(urllib.request.Request(self.url+path+query,headers=headers,method=method),timeout)
        if status==401:raise PlexError('Plex 拒绝了访问，请在这台电脑上重新登录 Plex Media Server。')
        if status>=400:raise PlexError(f'Plex 返回错误 {status}。')
        if accept!='application/json':return body
        try:return json.loads(body or b'{}').get('MediaContainer',{})
        except ValueError as exc:raise PlexError('Plex 返回了无法识别的内容。') from exc

    def server(self):
        root = self.call('/')
        return {'name':root.get('friendlyName',''),'version':root.get('version',''),'machine':root.get('machineIdentifier','')}

    def sections(self):
        return [{'id':str(d['key']),'kind':d.get('type',''),'title':d.get('title',''),
                 'locations':[l['path'] for l in d.get('Location',[]) if l.get('path')]}
                for d in self.call('/library/sections').get('Directory',[]) if d.get('type') in {'movie','show'}]

    def items(self,section,kind):
        """Every movie, show or episode of a library, a page at a time."""
        start = 0
        while True:
            page = self.call(f'/library/sections/{section}/all',{'type':TYPES[kind]},start=start)
            batch = page.get('Metadata',[])
            yield from batch
            start += len(batch)
            if not batch or start>=int(page.get('totalSize',start)):
                return

    def image(self,path,width=300,height=450):
        return self.call('/photo/:/transcode',{'url':path,'width':width,'height':height,'minSize':1,'upscale':1},accept='image/jpeg')

    def refresh(self,section,path=''):
        """Rescan a library, or only one folder of it."""
        self.call(f'/library/sections/{section}/refresh',{'path':path} if path else None)

    def empty_trash(self,section):
        self.call(f'/library/sections/{section}/emptyTrash',method='PUT')
