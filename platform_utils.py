"""Operating-system integration without Explorer command-line quoting ambiguities."""
import ctypes
import os
from pathlib import Path
import subprocess
import sys


def reveal_in_explorer(path):
    """Select the exact file or directory, including Unicode, commas and spaces."""
    path = Path(path).resolve(strict=True)
    if os.name!='nt':
        subprocess.Popen(['open' if sys.platform=='darwin' else 'xdg-open',str(path.parent)])
        return
    shell = ctypes.OleDLL('shell32')
    ole = ctypes.OleDLL('ole32')
    hresult = ctypes.c_long
    ole.CoInitialize.argtypes = [ctypes.c_void_p]
    ole.CoInitialize.restype = hresult
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole.CoTaskMemFree.restype = None
    ole.CoUninitialize.argtypes = []
    ole.CoUninitialize.restype = None
    shell.SHParseDisplayName.argtypes = [ctypes.c_wchar_p,ctypes.c_void_p,ctypes.POINTER(ctypes.c_void_p),ctypes.c_ulong,ctypes.c_void_p]
    shell.SHParseDisplayName.restype = hresult
    shell.SHOpenFolderAndSelectItems.argtypes = [ctypes.c_void_p,ctypes.c_uint,ctypes.c_void_p,ctypes.c_ulong]
    shell.SHOpenFolderAndSelectItems.restype = hresult
    initialized = ole.CoInitialize(None)
    pidl = ctypes.c_void_p()
    try:
        result = shell.SHParseDisplayName(str(path),None,ctypes.byref(pidl),0,None)
        if result<0:
            raise OSError(f'无法定位此文件（Windows 错误 0x{result & 0xffffffff:08X}）。')
        # Microsoft documents cidl=0 as selecting the full item in its parent folder.
        result = shell.SHOpenFolderAndSelectItems(pidl,0,None,0)
        if result<0:
            raise OSError(f'无法打开文件位置（Windows 错误 0x{result & 0xffffffff:08X}）。')
    finally:
        if pidl.value:
            ole.CoTaskMemFree(pidl)
        if initialized>=0:
            ole.CoUninitialize()


def volume_label(drive):
    """The name Explorer shows for a drive such as 'E:', or ''."""
    if os.name!='nt' or len(drive)!=2 or drive[1]!=':':
        return ''
    name = ctypes.create_unicode_buffer(261)
    ok = ctypes.windll.kernel32.GetVolumeInformationW(drive+'\\',name,len(name),None,None,None,None,0)
    return name.value if ok else ''
