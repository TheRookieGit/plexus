"""Name downloads the way Plex expects, after looking the title up in Plex's own online catalog.

Movies:  Title (Year)/Title (Year).mkv
Shows:   Title (Year)/Season 01/Title (Year) - S01E01.mkv"""
from __future__ import annotations

import json
import ntpath
import re
import urllib.parse
import urllib.request

from disk import SUBTITLE, VIDEO, guess_title, norm
from plex import PlexError, fetch

DISCOVER = 'https://discover.provider.plex.tv/library/search'
INVALID = str.maketrans({':':'：','?':'？','*':'＊','"':'＂','<':'＜','>':'＞','|':'｜','/':'／','\\':'＼'})
CHINESE = {'零':0,'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10}
EPISODE = [re.compile(p,re.I) for p in (
    r'(?<![a-z0-9])s(?P<season>\d{1,2})[ ._-]?e(?P<episode>\d{1,4})(?!\d)',
    r'(?<!\d)(?P<season>\d{1,2})x(?P<episode>\d{1,3})(?!\d)',
    r'第\s*(?P<episode>\d{1,4})\s*[集话話]',
    r'(?<![a-z0-9])(?:ep?|episode)[ ._-]?(?P<episode>\d{1,4})(?!\d)',
    r'[\[【](?P<episode>\d{1,3})(?:v\d)?[\]】]',
    r'(?:^|[ ._-])-?[ ._]?(?P<episode>\d{2,3})(?:v\d)?(?=[ ._\[(（-]|$)',
)]
SEASON = [re.compile(p,re.I) for p in (r'(?<![a-z0-9])(?:season|s)[ ._-]?(?P<n>\d{1,2})(?![0-9])',r'第\s*(?P<n>[\d一二两三四五六七八九十]{1,3})\s*季')]
PART = re.compile(r'(?<![a-z])(?:cd|part|pt|disc|disk)[ ._-]?(\d{1,2})(?!\d)',re.I)
NOT_EPISODE = re.compile(r'(?<!\d)(?:2160|1080|720|576|480|264|265)(?!\d)|(?<!\d)(?:19|20)\d\d(?!\d)')


def safe(name):
    """A name Windows accepts as one path segment."""
    return re.sub(r'\s+',' ',str(name).translate(INVALID)).strip().rstrip('. ') or '未命名'


def chinese_number(text):
    if text.isdigit():return int(text)
    if text=='十':return 10
    if text.startswith('十'):return 10+CHINESE.get(text[1:],0)
    if '十' in text:
        tens,_,ones = text.partition('十')
        return CHINESE.get(tens,0)*10+CHINESE.get(ones,0)
    return CHINESE.get(text,0)


def season_of(text):
    for pattern in SEASON:
        match = pattern.search(text)
        if match:return chinese_number(match.group('n'))
    return None


def episode_of(name):
    """(season, episode) read from a file name; either may be None."""
    stem = ntpath.splitext(name)[0]
    for pattern in EPISODE:
        for match in pattern.finditer(stem):
            if pattern is EPISODE[-1] and NOT_EPISODE.fullmatch(match.group('episode')):continue
            groups = match.groupdict()
            return (int(groups['season']) if groups.get('season') else None),int(groups['episode'])
    return None,None


def kind_of(path):
    ext = ntpath.splitext(path)[1].lower()
    return 'video' if ext in VIDEO else 'subtitle' if ext in SUBTITLE else 'other'


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


def guess(names,hint=''):
    """What a download most likely is, from its folder and file names: {title, year, kind}.
    The folder holding the videos names a show; a movie's own file name wins when it carries the year."""
    videos = [n.replace('\\','/') for n in names if kind_of(n)=='video']
    numbered = sum(1 for n in videos if episode_of(n.rsplit('/',1)[-1])[1] is not None)
    kind = 'show' if len(videos)>1 and numbered>=max(2,len(videos)//2) else 'movie'
    # The deepest folder holding every video, above any season folders.
    common = []
    for parts in zip(*[v.split('/')[:-1] for v in videos]) if videos else []:
        if len(set(parts))!=1:break
        common.append(parts[0])
    folder = '/'.join(common)
    while folder and season_of(folder.rsplit('/',1)[-1]) is not None:
        folder = folder.rsplit('/',1)[0] if '/' in folder else ''
    folder_name = folder.rsplit('/',1)[-1] if folder else ''
    file_name = videos[0].rsplit('/',1)[-1] if videos else (names[0].rsplit('/',1)[-1] if names else '')
    options = [n for n in (file_name if kind=='movie' else '',folder_name,hint,file_name) if n]
    dated = [n for n in options if guess_title(n)[1]]
    title,year = guess_title(dated[0] if dated else options[0] if options else '')
    title = re.split(r'[ ._-]+(?:S\d{1,2}|Season|第[\d一二三四五六七八九十]+季)',title,flags=re.I)[0].strip() or title
    return {'title':title,'year':year,'kind':kind}


def plan(files,kind,title,year,location,existing=''):
    """Where each downloaded file goes. files: [{id, path (relative to the share), season?, episode?}].
    Returns the same files with 'target' set, or '' to leave a file out."""
    base = safe(f'{title} ({year})' if year else title)
    root = existing or ntpath.join(location,base)
    videos = [f for f in files if kind_of(f['path'])=='video']
    result = []
    stems = {}  # A video's original stem -> its new name without extension, so subtitles follow it.
    if kind=='movie':
        parts = {f['id']:PART.search(ntpath.basename(f['path'])) for f in videos}
        main = max(videos,key=lambda f:f.get('size',0)) if videos else None
        for index,f in enumerate(sorted(videos,key=lambda f:f['path']),1):
            part = parts[f['id']]
            name = f'{base} - pt{int(part.group(1))}' if part and len(videos)>1 else base if f is main else f'{base} - {index}'
            stems[ntpath.splitext(ntpath.basename(f['path']))[0].casefold()] = name
            result.append(f|{'target':ntpath.join(root,name+ntpath.splitext(f['path'])[1].lower())})
    else:
        for f in videos:
            season,episode = f.get('season'),f.get('episode')
            if season is None or episode is None:
                found_season,found_episode = episode_of(ntpath.basename(f['path']))
                season = season if season is not None else found_season if found_season is not None else season_of(f['path']) or 1
                episode = episode if episode is not None else found_episode
            folder = ntpath.join(root,f'Season {int(season):02d}')
            name = f'{base} - S{int(season):02d}E{int(episode):02d}' if episode is not None else safe(ntpath.splitext(ntpath.basename(f['path']))[0])
            stems[ntpath.splitext(ntpath.basename(f['path']))[0].casefold()] = (folder,name)
            result.append(f|{'season':int(season),'episode':episode,'target':ntpath.join(folder,name+ntpath.splitext(f['path'])[1].lower())})
    for f in files:
        if kind_of(f['path'])!='subtitle':continue
        stem = ntpath.splitext(ntpath.basename(f['path']))[0]
        match = max((s for s in stems if stem.casefold().startswith(s)),key=len,default=None)
        tail = stem[len(match):] if match else ''
        if match is None and kind=='show':
            _,episode = episode_of(stem)
            match = next((s for s,(folder,name) in stems.items() if name.endswith(f'E{episode:02d}')),None) if episode is not None else None
            tail = '.'+stem.rsplit('.',1)[1] if match and '.' in stem else ''
        if match is None and kind=='movie' and len(stems)==1:match,tail = next(iter(stems)),''
        if match is None:
            result.append(f|{'target':ntpath.join(root,safe(ntpath.basename(f['path'])))});continue
        new = stems[match]
        folder,name = new if isinstance(new,tuple) else (root,new)
        result.append(f|{'target':ntpath.join(folder,name+safe(tail)+ntpath.splitext(f['path'])[1].lower())})
    # Two files must never land on one name.
    seen = {}
    for f in result:
        key = norm(f['target'])
        if key in seen:
            stem,ext = ntpath.splitext(f['target'])
            count = 2
            while norm(f'{stem} ({count}){ext}') in seen:count += 1
            f['target'] = f'{stem} ({count}){ext}'
        seen[norm(f['target'])] = f
    return result
