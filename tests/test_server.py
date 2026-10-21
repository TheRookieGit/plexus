import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from app_runtime import AppHTTPServer
from server import Handler, Library


class AccessTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server = AppHTTPServer(('127.0.0.1',0),Handler)
        self.server.library = Library(Path(self.temp.name))
        self.server.allowed_origins = set()
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def call(self,path,data=None,headers=None):
        request = urllib.request.Request(self.base+path,json.dumps(data).encode() if data is not None else None,
                                         {'Content-Type':'application/json',**(headers or {})})
        try:
            with urllib.request.urlopen(request) as response:return response.status,json.loads(response.read())
        except urllib.error.HTTPError as exc:
            with exc:return exc.code,json.loads(exc.read())

    def test_local_requests_need_no_password(self):
        status,body = self.call('/api/items')
        self.assertEqual(status,200)
        self.assertEqual(body['total'],0)

    def test_tunnel_requests_must_sign_in(self):
        tunnel = {'X-Forwarded-For':'203.0.113.5'}
        self.assertEqual(self.call('/api/items',headers=tunnel)[0],401)
        self.assertEqual(self.call('/api/access/password',{'password':'secret1'})[0],200)
        self.assertEqual(self.call('/api/login',{'password':'wrong!!'},tunnel)[0],403)
        status,body = self.call('/api/login',{'password':'secret1'},tunnel)
        self.assertEqual(status,200)
        signed = tunnel|{'Authorization':'Bearer '+body['token']}
        self.assertEqual(self.call('/api/items',headers=signed)[0],200)
        # Changing settings or opening Explorer stays on the Windows machine.
        self.assertEqual(self.call('/api/settings',{'plex_url':'http://x'},signed)[0],403)
        self.assertEqual(self.call('/api/reveal',{'path':'C:\\'},signed)[0],403)


if __name__=='__main__':
    unittest.main()
