"""Look titles up in Plex's own online catalog."""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

from plex import PlexError, fetch

DISCOVER = 'https://discover.provider.plex.tv/library/search'


def search(token,query,kind='',limit=8):
    """Titles in Plex's online catalog: [{title, year, kind, guid, thumb, summary}]."""
    query = str(query or '').strip()
    if not query:return []
    types = {'movie':'movies','show':'tv'}.get(kind,'movies,tv')
    url = DISCOVER+'?'+urllib.parse.urlencode({'query':query,'limit':limit,'searchTypes':types,'searchProviders':'discover','includeMetadata':1})
    request = urllib.request.Request(url,headers={'X-Plex-Token':token,'Accept':'application/json','X-Plex-Language':'zh-CN',
                                                  'X-Plex-Product':'Plexus','X-Plex-Client-Identifier':'plexus'})
    status,_,body = fetch(request,20)
    if status>=400:raise PlexError(f'Plex 在线搜索暂时不可用（{status}）。')
    found = []
    for group in json.loads(body or b'{}').get('MediaContainer',{}).get('SearchResults',[]):
        for result in group.get('SearchResult',[]):
            meta = result.get('Metadata') or {}
            if meta.get('type') in {'movie','show'} and meta.get('title'):
                found.append({'title':meta['title'],'original_title':meta.get('originalTitle',''),'year':meta.get('year'),'kind':meta['type'],
                              'guid':meta.get('guid',''),'thumb':meta.get('thumb',''),'summary':meta.get('summary','')[:300]})
    return found


def rank(results,title,year=None):
    """Exact titles first, then those of the right year; otherwise keep Plex's order."""
    key = title.casefold().replace(' ','')
    def score(item):
        names = {item['title'].casefold().replace(' ',''),(item.get('original_title') or '').casefold().replace(' ','')}
        exact = key in names
        close = any(key and (key in n or n and n in key) for n in names)
        return (-exact,-close,0 if not year or item['year']==year else 1 if item['year'] and abs(item['year']-year)<=1 else 2)
    return sorted(results,key=score)
