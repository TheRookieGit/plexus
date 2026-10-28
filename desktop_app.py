"""Plexus Windows app: the service in the system tray, the catalog in an Edge app window.

Closing the window leaves the service running, so downloads continue and the GitHub web page can
still reach this computer. The app updates itself from the latest GitHub build, so it is installed only once.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import webbrowser
import zipfile

# Before server, which imports the shared code from the my-collections submodule.
sys.path.insert(0,str(Path(__file__).resolve().parent/'vendor'/'my-collections'))
import server

APP_NAME = 'Plexus'
REPO = 'TheRookieGit/plexus'
RELEASE_TAG = 'app-latest'
ASSET = 'Plexus-windows.zip'
WEB_ORIGIN = 'https://therookiegit.github.io'
RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
RUN_VALUE = 'Plexus'
UPDATE_INTERVAL = 600
# Downloads resume after a restart, so a pending update waits for them only this long.
DOWNLOAD_WAIT = 7200
BUNDLE = Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))
ICON = BUNDLE/'assets'/'plexus.ico'
FROZEN = getattr(sys,'frozen',False)
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
NO_WINDOW = 0x08000000


def build_info():
    try:return json.loads((BUNDLE/'build_info.json').read_text(encoding='utf-8'))
    except (OSError,ValueError):return {}


# Window ---------------------------------------------------------------------------------------
def find_edge():
    for base in [os.environ.get('PROGRAMFILES(X86)'),os.environ.get('PROGRAMFILES'),os.environ.get('LOCALAPPDATA')]:
        if base:
            path = Path(base)/'Microsoft'/'Edge'/'Application'/'msedge.exe'
            if path.is_file():return path
    return None


def open_window(url,data_dir):
    """A borderless Edge app window with its own profile; falls back to the default browser."""
    edge = find_edge()
    if not edge:
        webbrowser.open(url)
        return
    profile = Path(data_dir)/'window'
    subprocess.Popen([str(edge),f'--app={url}',f'--user-data-dir={profile}','--no-first-run',
                      '--no-default-browser-check','--window-size=1400,900'],creationflags=NO_WINDOW)


# Start with Windows ---------------------------------------------------------------------------
def launch_command():
    if FROZEN:return f'"{sys.executable}" --minimized'
    return f'"{sys.executable}" "{Path(__file__).resolve()}" --minimized'


def autostart_enabled():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,RUN_KEY) as key:
            return winreg.QueryValueEx(key,RUN_VALUE)[0]==launch_command()
    except OSError:
        return False


def set_autostart(enabled):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,RUN_KEY,0,winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key,RUN_VALUE,0,winreg.REG_SZ,launch_command())
        else:
            try:winreg.DeleteValue(key,RUN_VALUE)
            except FileNotFoundError:pass


# Updates --------------------------------------------------------------------------------------
def latest_release():
    request = urllib.request.Request(f'https://api.github.com/repos/{REPO}/releases/tags/{RELEASE_TAG}',
                                     headers={'Accept':'application/vnd.github+json','User-Agent':'Plexus'})
    with urllib.request.urlopen(request,timeout=20) as response:
        release = json.load(response)
    asset = next((a for a in release.get('assets',[]) if a.get('name')==ASSET),None)
    return release.get('target_commitish',''),asset


def stage_update(asset,data_dir):
    """Download and unpack the new build; return the staged exe."""
    staging = Path(data_dir)/'update'
    shutil.rmtree(staging,ignore_errors=True)
    staging.mkdir(parents=True)
    archive = staging/'update.zip'
    request = urllib.request.Request(asset['browser_download_url'],headers={'User-Agent':'Plexus'})
    with urllib.request.urlopen(request,timeout=600) as response,open(archive,'wb') as target:
        shutil.copyfileobj(response,target,1024*1024)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(staging/'new')
    archive.unlink()
    exe = next((staging/'new').glob('*/*.exe'),None)
    if not exe:raise OSError('更新包中没有找到程序。')
    return exe


def wait_for_exit(pid,timeout=60):
    # os.kill(pid,0) would terminate the process on Windows, so wait on its handle instead.
    import ctypes
    kernel = ctypes.WinDLL('kernel32',use_last_error=True)
    handle = kernel.OpenProcess(0x00100000,False,pid)  # SYNCHRONIZE
    if not handle:return
    try:kernel.WaitForSingleObject(handle,int(timeout*1000))
    finally:kernel.CloseHandle(handle)


def finish_update(target,pid):
    """Run from the staged copy: wait for the old app to exit, replace its files, restart it."""
    target = Path(target)
    source = Path(sys.executable).resolve().parent
    wait_for_exit(int(pid))
    for attempt in range(20):
        try:
            shutil.rmtree(target/'_internal',ignore_errors=False)
            for exe in target.glob('*.exe'):exe.unlink()
            shutil.copytree(source,target,dirs_exist_ok=True)
            break
        except OSError:
            # Antivirus scanners briefly lock freshly written files.
            if attempt==19:raise
            time.sleep(1)
    exe = next(target.glob('*.exe'))
    subprocess.Popen([str(exe),'--updated'],cwd=str(target))


class Updater:
    def __init__(self,service,data_dir,notify,quit_app):
        self.service = service
        self.data_dir = data_dir
        self.notify = notify
        self.quit_app = quit_app
        self.current = build_info().get('sha','')
        self.wake = threading.Event()
        self.status = '尚未检查更新' if self.current else '开发版本，不自动更新'
        self.waiting_since = None
        self.pending = False

    def busy(self):
        lib = self.service.library
        # Moving files into the library or saving into a drive must finish; a download can stop and resume.
        if lib.downloads.saving or lib.downloads.applying():
            return True
        if lib.downloads.active:
            self.waiting_since = self.waiting_since or time.time()
            return time.time()-self.waiting_since<DOWNLOAD_WAIT
        self.waiting_since = None
        return False

    def check(self,manual=False):
        if not FROZEN or not self.current:
            if manual:self.notify('从源码运行时不自动更新，请使用 git pull。')
            return
        try:
            latest,asset = latest_release()
        except OSError:
            self.status = '无法连接 GitHub，稍后重试'
            if manual:self.notify(self.status)
            return
        if not latest or latest==self.current or not asset:
            self.status = f'已是最新版本（{self.current[:7]}）'
            if manual:self.notify(self.status)
            return
        if self.busy():
            self.status = '有新版本，等待下载完成后更新'
            self.pending = True
            if manual:self.notify(self.status)
            return
        self.status = '正在下载新版本…'
        try:
            exe = stage_update(asset,self.data_dir)
        except (OSError,zipfile.BadZipFile) as exc:
            self.status = f'下载更新失败：{exc}'
            if manual:self.notify(self.status)
            return
        subprocess.Popen([str(exe),'--finish-update',str(APP_DIR),'--wait-pid',str(os.getpid())],cwd=str(exe.parent))
        self.quit_app()

    def run(self):
        time.sleep(20)
        while True:
            manual = self.wake.is_set()
            self.wake.clear()
            self.pending = False
            self.check(manual)
            # While an update waits for a task, look again every minute instead of every ten.
            self.wake.wait(60 if self.pending else UPDATE_INTERVAL)


# Main -----------------------------------------------------------------------------------------
def main():
    parser = server.build_parser()
    parser.add_argument('--minimized',action='store_true',help='启动后只显示在系统托盘')
    parser.add_argument('--no-window',action='store_true',help='只运行后台服务（用于自动测试）')
    parser.add_argument('--updated',action='store_true',help=argparse_hidden())
    parser.add_argument('--finish-update',help=argparse_hidden())
    parser.add_argument('--wait-pid',help=argparse_hidden())
    args = parser.parse_args()
    if args.finish_update:
        return finish_update(args.finish_update,args.wait_pid)
    if args.updated:
        shutil.rmtree(Path(args.data_dir)/'update',ignore_errors=True)
    args.allow_origin = args.allow_origin or [WEB_ORIGIN]
    url = f'http://127.0.0.1:{args.port}'
    service = server.start(args)
    if service is None:
        # Already running: just show the catalog.
        if not args.no_window and not args.minimized:open_window(url,args.data_dir)
        return
    if args.no_window:
        try:service.serve_forever()
        finally:service.server_close()
        return

    import pystray
    from PIL import Image

    stopped = threading.Event()

    def quit_app(*_):
        if stopped.is_set():return
        stopped.set()
        tray.stop()
        threading.Thread(target=service.shutdown,daemon=True).start()

    def notify(message):
        try:tray.notify(message,APP_NAME)
        except Exception:pass

    def serve():
        service.serve_forever()
        # A newer copy took over the port (see app_runtime.reuse_or_reload): leave quietly.
        quit_app()

    updater = Updater(service,args.data_dir,notify,quit_app)
    service.on_show = lambda:open_window(url,args.data_dir)
    version = build_info().get('sha','')[:7] or '开发版'
    tray = pystray.Icon('plexus',Image.open(ICON),APP_NAME,menu=pystray.Menu(
        pystray.MenuItem('打开 Plexus',lambda *_:open_window(url,args.data_dir),default=True),
        pystray.MenuItem('下载',lambda *_:open_window(url+'/downloads.html',args.data_dir)),
        pystray.MenuItem('开机自动启动',lambda *_:set_autostart(not autostart_enabled()),checked=lambda _:autostart_enabled()),
        pystray.MenuItem('检查更新',lambda *_:updater.wake.set()),
        pystray.MenuItem(lambda _:f'版本 {version} · {updater.status}',None,enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('退出',quit_app)))
    threading.Thread(target=serve,daemon=True).start()
    threading.Thread(target=updater.run,daemon=True).start()
    if not args.minimized:open_window(url,args.data_dir)
    if args.updated:threading.Timer(3,lambda:notify(f'已更新到新版本 {version}')).start()
    tray.run()
    service.server_close()


def argparse_hidden():
    import argparse
    return argparse.SUPPRESS


if __name__=='__main__':
    main()
