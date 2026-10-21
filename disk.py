"""Find the videos and subtitles actually on disk under each Plex library folder."""
from __future__ import annotations

import ntpath
import os
import re

VIDEO = {'.mkv','.mp4','.m4v','.avi','.rmvb','.rm','.ts','.m2ts','.mts','.mov','.wmv','.flv','.webm','.mpg','.mpeg','.vob','.iso'}
SUBTITLE = {'.ass','.ssa','.srt','.sub','.idx','.sup','.vtt'}
# NAS and macOS bookkeeping that is never media.
SKIPPED = {'@eadir','#recycle','#snapshot','$recycle.bin','system volume information','_gsdata_','.appledouble'}
RESOLUTIONS = ['4k','2160','1080','720','576','480','sd']


def norm(path):
    """One spelling for every way Windows may write a path, so Plex's and the disk's paths can be compared."""
    return ntpath.normcase(ntpath.normpath(str(path)))


def drive(path):
    return ntpath.splitdrive(str(path))[0].upper() or str(path)


def rank(resolution):
    """Lower is sharper; unknown sorts last."""
    resolution = str(resolution or '').lower().rstrip('p')
    return RESOLUTIONS.index(resolution) if resolution in RESOLUTIONS else len(RESOLUTIONS)


def walk(location):
    """(path, size, mtime, kind) for every video and subtitle below a library folder."""
    pending = [location]
    while pending:
        folder = pending.pop()
        try:entries = list(os.scandir(folder))
        except OSError:continue
        for entry in entries:
            name = entry.name
            if name.startswith('.') or name.lower() in SKIPPED:
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    pending.append(entry.path)
                    continue
                ext = ntpath.splitext(name)[1].lower()
                if ext not in VIDEO and ext not in SUBTITLE:
                    continue
                stat = entry.stat()
            except OSError:
                continue
            yield entry.path,stat.st_size,stat.st_mtime,'video' if ext in VIDEO else 'subtitle'


NOISE = re.compile(r'''(?ix)\b(
    2160p|1080p|720p|480p|4k|uhd|hdr10?\+?|hdr|dv|dolby\.?vision|sdr|10bit|8bit|
    blu-?ray|bdrip|brrip|remux|web-?dl|web-?rip|webrip|hdtv|dvdrip|hdrip|
    h\.?26[45]|x26[45]|hevc|avc|aac\d?(\.\d)?|ac3|dts(-hd)?(\.ma)?|truehd|atmos|flac|ddp?\d?(\.\d)?|ma\d\.\d|\d\.\d|
    \d+audio|chs|cht|eng|中字|国语|粤语|双语|中英|简繁|内封|外挂)\b.*$''')


def guess_title(name):
    """(title, year) read from a folder or file name such as '[007：大破天幕杀机]Skyfall.2012.BluRay.2160p'."""
    stem = ntpath.splitext(name)[0] if ntpath.splitext(name)[1].lower() in VIDEO else name
    bracketed = re.match(r'^\s*[\[【]([^\]】]+)[\]】]',stem)
    year = re.search(r'(?<!\d)(19[2-9]\d|20[0-4]\d)(?!\d)',stem)
    title = bracketed.group(1) if bracketed else stem
    if not bracketed:
        if year and year.start()>0:title = title[:year.start()]
        title = NOISE.sub('',re.sub(r'[._]+',' ',title))
        title = re.sub(r'\bS\d{1,2}(E\d{1,3})?\b.*$','',title,flags=re.I)
    title = re.sub(r'[\s\-–—(（\[【]+$','',title.strip()).strip() or stem
    return title,int(year.group(1)) if year else None
