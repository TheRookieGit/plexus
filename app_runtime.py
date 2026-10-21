"""Reuse the current app, but replace an idle server when its code changes."""
import hashlib
import json
import os
from pathlib import Path
import socket
import time
import urllib.error
import urllib.request
import uuid
from http.server import ThreadingHTTPServer


class AppHTTPServer(ThreadingHTTPServer):
    # Windows SO_REUSEADDR permits two servers to bind the same address.
    allow_reuse_address = os.name != 'nt'

    def server_bind(self):
        if os.name == 'nt':
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def code_fingerprint(base):
    base = Path(base).resolve()
    paths = sorted([*base.glob('*.py'), *(p for folder in ('web',) for p in (base/folder).rglob('*') if p.is_file())])
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(base).as_posix().encode('utf-8'))
        digest.update(b'\0')
        digest.update(path.read_bytes())
    return digest.hexdigest()


def runtime_identity(base, data_dir, host='127.0.0.1'):
    return {'app': 'plexus', 'protocol': 1, 'host': host, 'base': str(Path(base).resolve()),
            'data_dir': str(Path(data_dir).resolve()), 'fingerprint': code_fingerprint(base),
            'instance': uuid.uuid4().hex, 'pid': os.getpid()}


def reuse_or_reload(port, expected, root=None):
    """Return True to reuse; False after requesting a matching app's shutdown."""
    url = f'http://127.0.0.1:{port}'
    try:
        with urllib.request.urlopen(url+'/api/runtime', timeout=10) as response:
            if not response.headers.get('Server', '').startswith('Plexus/'):
                raise ValueError('Not Plexus')
            current = json.loads(response.read(16384))
            if not isinstance(current, dict) or not isinstance(current.get('instance'), str) or not current['instance']:
                raise ValueError('Invalid app identity')
    except (OSError, ValueError) as exc:
        if isinstance(exc, urllib.error.HTTPError):
            exc.close()
        raise SystemExit(f'端口 {port} 已被占用，或仍有旧版 Plexus 在运行。请关闭原来的 Plexus 终端后重试。') from exc
    if (current.get('app') != 'plexus' or current.get('protocol') != 1 or
            any(os.path.normcase(current.get(key, '')) != os.path.normcase(expected[key]) for key in ['base', 'data_dir'])):
        raise SystemExit(f'端口 {port} 已被其他程序或另一份 Plexus 占用，请用 --port 指定其他端口。')
    root_matches = root is None or os.path.normcase(current.get('root', '')) == os.path.normcase(str(Path(root).resolve()))
    host_matches = current.get('host', '127.0.0.1') == expected.get('host', '127.0.0.1')
    if current.get('fingerprint') == expected['fingerprint'] and root_matches and host_matches:
        return True
    request = urllib.request.Request(url+'/api/runtime/reload',
        data=json.dumps({'instance': current['instance']}).encode(),
        headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            result = json.loads(response.read(16384))
        if not result.get('ok'):
            raise ValueError('Restart refused')
    except urllib.error.HTTPError as exc:
        try:
            message = json.loads(exc.read(16384)).get('error', '后台服务暂时不能重启。')
        except ValueError:
            message = '后台服务暂时不能重启。'
        finally:
            exc.close()
        raise SystemExit(message) from exc
    except (OSError, ValueError) as exc:
        raise SystemExit('无法重启旧版 Plexus，请关闭原来的 Plexus 终端后重试。') from exc
    print('检测到程序更新，正在切换到新版 Plexus…', flush=True)
    return False


def bind_server(port, handler, identity, root=None):
    try:
        server = AppHTTPServer((identity.get('host', '127.0.0.1'), port), handler)
    except OSError:
        if reuse_or_reload(port, identity, root):
            return None
        deadline = time.monotonic()+8
        while True:
            try:
                server = AppHTTPServer((identity.get('host', '127.0.0.1'), port), handler)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise SystemExit('旧版 Plexus 尚未退出，请稍后重新启动。') from exc
                time.sleep(.1)
    server.runtime = identity
    return server
