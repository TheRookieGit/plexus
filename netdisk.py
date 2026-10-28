"""Quark (夸克网盘) and Baidu (百度网盘) share links for Plexus: which shared files to take, and the drives themselves.

Speaking to the drives, and the sign-ins themselves, come from collectionkit (the my-collections submodule), which
书屿 and the wish hub share: signing in or out in one of them does so in all. Shares are saved into your own drive
in a Plexus转存 folder. The queue, resuming and naming live in downloads.py."""
from __future__ import annotations

import json
from pathlib import PurePosixPath

from collectionkit.netdisk import Drives, NetDiskError, Unreachable, links
from disk import SUBTITLE, VIDEO

__all__ = ['FOLDER','NetDisk','NetDiskError','Unreachable','links','wanted']
FOLDER = 'Plexus转存'


def wanted(name,size):
    """'video', 'subtitle', or '' for files Plexus leaves out."""
    ext = PurePosixPath(name).suffix.lower()
    if size<=0:return ''
    return 'video' if ext in VIDEO else 'subtitle' if ext in SUBTITLE else ''


class NetDisk(Drives):
    """The drives signed in to for every app on this computer, saving shares into Plexus's own folder."""
    def __init__(self,lib,store=None):
        super().__init__(FOLDER,store)
        # Sign-ins from before they were shared move over once; the old rows stay, so an older Plexus still has them.
        with lib.db() as db:
            rows = db.execute("SELECT key,value FROM settings WHERE key IN ('netdisk_quark','netdisk_baidu')").fetchall()
        saved = {}
        for row in rows:
            try:saved[row['key'].removeprefix('netdisk_')] = dict(json.loads(row['value']))
            except (ValueError,TypeError):pass
        self.adopt(saved)
