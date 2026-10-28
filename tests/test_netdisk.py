from email.message import Message
import json
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.parse

from collectionkit.credentials import Credentials
from collectionkit.secret import protect_secret
import netdisk
from netdisk import NetDisk, NetDiskError, links, wanted
from tests.test_catalog import Fixture

EPISODE = b'quark episode one ' * 50
SUBTITLE = '字幕'.encode()
MOVIE = b'baidu movie ' * 40


class FakeDrives:
    """Quark's and Baidu's web APIs as netdisk.py sees them; the shapes come from their web pages."""
    def __init__(self):
        self.calls,self.lock = [],threading.Lock()
        self.quark_polls = self.baidu_polls = self.quark_checks = 0
        self.quark_logged_in = True
        self.quark_folder = self.baidu_folder = False
        self.saved = {}
        self.drop_once = True

    def reply(self,data=None,status=200,raw=None,cookies=(),location=None,sink=None,content=None,start=0):
        headers = Message()
        for cookie in cookies:headers['Set-Cookie'] = cookie
        if location:headers['Location'] = location
        if content is not None:
            sink.write(content[start:]);return (206 if start else 200),headers,b''
        return status,headers,raw if raw is not None else json.dumps(data,ensure_ascii=False).encode()

    def __call__(self,request,timeout,sink=None):
        url = urllib.parse.urlsplit(request.full_url)
        query = {k:v[-1] for k,v in urllib.parse.parse_qs(url.query,keep_blank_values=True).items()}
        cookies = dict(part.split('=',1) for part in (request.get_header('Cookie') or '').split('; ') if part)
        body = request.data or b''
        form = {k:v[-1] for k,v in urllib.parse.parse_qs(body.decode()).items()} if 'form-urlencoded' in (request.get_header('Content-type') or '') else {}
        data = json.loads(body) if body and not form else {}
        start = int((request.get_header('Range') or 'bytes=0-')[6:].split('-')[0])
        call = {'host':url.netloc,'path':url.path,'query':query,'cookies':cookies,'data':data,'form':form,'agent':request.get_header('User-agent'),'start':start}
        with self.lock:self.calls.append(call)
        if url.netloc.endswith('quark.cn') or url.netloc.endswith('quark.test'):return self.quark(url,query,cookies,data,sink,start)
        return self.baidu(url,query,cookies,form,sink,call['agent'],start)

    def quark(self,url,query,cookies,data,sink,start):
        if url.path=='/cas/ajax/getTokenForQrcodeLogin':
            self.quark_polls += 1
            return self.reply({'status':2000000,'data':{'members':{'token':f'token-{self.quark_polls}'}}})
        if url.path=='/cas/ajax/getServiceTicketByQrcodeToken':
            self.quark_checks += 1
            if self.quark_checks==1:raise netdisk.Unreachable('dropped connection')
            # The first QR code expires before anyone scans it; the second one is scanned.
            if query['token']=='token-1':return self.reply({'status':50004002,'message':'Token Not Found'})
            return self.reply({'status':2000000,'data':{'members':{'service_ticket':'ticket'}}})
        if url.path=='/account/info':
            assert query['st']=='ticket'
            return self.reply({'success':True,'code':'OK','data':{'nickname':'夸克用户'}},cookies=['__pus=pus-cookie; Domain=.quark.cn; Path=/; HttpOnly'])
        if url.netloc=='dl.quark.test':return self.reply(status=302,location='https://cdn.quark.test'+url.path,raw=b'')
        if url.netloc=='cdn.quark.test':
            content = {'/s-f1':EPISODE,'/s-f2':SUBTITLE}[url.path]
            if url.path=='/s-f1' and self.drop_once:
                # The connection drops halfway through the episode once; the next request asks for the rest.
                self.drop_once = False
                sink.write(content[:300])
                raise netdisk.Unreachable('dropped')
            return self.reply(sink=sink,content=content,start=start)
        if not self.quark_logged_in or cookies.get('__pus')!='pus-cookie':return self.reply({'status':401,'code':31001,'message':'require login [guest]'},401)
        path = url.path.removeprefix('/1/clouddrive/')
        if path=='share/sharepage/token':
            if data['pwd_id']!='qshare':return self.reply({'status':404,'code':41006,'message':'分享不存在'},404)
            return self.reply({'status':200,'code':0,'data':{'stoken':'stoken','title':'某剧'}})
        if path=='share/sharepage/detail':
            assert query['stoken']=='stoken'
            items = {'0':[{'fid':'d1','file_name':'某剧.2024','dir':True},{'fid':'f3','file_name':'封面.jpg','dir':False,'size':10,'share_fid_token':'t3'}],
                     'd1':[{'fid':'f1','file_name':'某剧.S01E01.2160p.mkv','dir':False,'size':len(EPISODE),'share_fid_token':'t1'},
                           {'fid':'f2','file_name':'某剧.S01E01.2160p.chs.ass','dir':False,'size':len(SUBTITLE),'share_fid_token':'t2'}]}[query['pdir_fid']]
            return self.reply({'status':200,'code':0,'data':{'list':items},'metadata':{'_total':len(items)}})
        if path=='file/info/path_list':return self.reply({'status':200,'code':0,'data':[{'fid':'mine'}] if self.quark_folder else []})
        if path=='file':
            self.quark_folder = True;return self.reply({'status':200,'code':0,'data':{'fid':'mine'}})
        if path=='share/sharepage/save':
            assert data['to_pdir_fid']=='mine' and data['fid_token_list']==[{'f1':'t1','f2':'t2'}[f] for f in data['fid_list']]
            self.saved['quark'] = data
            return self.reply({'status':200,'code':0,'data':{'task_id':'task'}})
        if path=='task':
            return self.reply({'status':200,'code':0,'data':{'status':2,'save_as':{'save_as_top_fids':['s-'+f for f in self.saved['quark']['fid_list']]}}})
        if path=='file/download':
            return self.reply({'status':200,'code':0,'data':[{'fid':f,'download_url':'https://dl.quark.test/'+f} for f in data['fids']]})
        return self.reply({'status':404,'code':1,'message':'unknown'},404)

    def baidu(self,url,query,cookies,form,sink,agent,start):
        signed = cookies.get('BDUSS')=='bduss' and cookies.get('STOKEN')=='pan-stoken'
        if url.netloc=='pan.baidu.com' and url.path=='/':return self.reply(raw=b'<html>',cookies=['BAIDUID=b; Domain=.baidu.com; Path=/'])
        if url.path=='/v2/api/getqrcode':return self.reply({'imgurl':'passport.baidu.com/v2/api/qrcode?sign=sign','errno':0,'sign':'sign'})
        if url.path=='/v2/api/qrcode':return self.reply(raw=b'\x89PNG fake')
        if url.path=='/channel/unicast':
            self.baidu_polls += 1
            value = [{'status':1},{'status':0,'v':'temporary'}][min(self.baidu_polls,2)-1]
            return self.reply({'errno':0,'channel_id':'sign','channel_v':json.dumps(value)})
        if url.path=='/v3/login/main/qrbdusslogin':
            assert query['bduss']=='temporary'
            # Like Baidu's own reply: one key is single-quoted, so it is not valid JSON.
            return self.reply(raw=b'{"errInfo":{"no": "0"}, "code": "110000", \'data\': {"u": ""}}',cookies=['BDUSS=bduss; Domain=.baidu.com; Path=/; HttpOnly'])
        if url.path=='/v3/login/api/auth/':
            assert cookies.get('BDUSS')=='bduss'
            return self.reply(status=302,location='https://pan.baidu.com/disk/main',raw=b'')
        if url.path=='/disk/main':return self.reply(raw=b'<html>',cookies=['STOKEN=pan-stoken; Domain=.pan.baidu.com; Path=/'])
        if url.netloc=='d.pcs.baidu.com':
            assert agent=='pan.baidu.com',agent
            return self.reply(status=302,location='https://nd.baidupcs.test'+url.path,raw=b'')
        if url.netloc=='nd.baidupcs.test':return self.reply(sink=sink,content=MOVIE,start=start)
        if url.path=='/share/verify':
            if form['pwd']!='abcd':return self.reply(raw=b'\n\n{"errno":-9,"err_msg":""}')
            return self.reply({'errno':0,'randsk':'rk%3D'})
        if url.path=='/share/list':
            if cookies.get('BDCLND')!='rk%3D':return self.reply({'errno':9019,'errmsg':'need verify'})
            items = [{'fs_id':11,'server_filename':'Inception.2010.1080p.mkv','isdir':'0','size':str(len(MOVIE)),'path':'/资源/Inception.2010.1080p.mkv'},
                     {'fs_id':12,'server_filename':'sub','isdir':'1','size':'0','path':'/资源/sub'}] if query.get('root') else \
                    [{'fs_id':13,'server_filename':'说明.pdf','isdir':'0','size':'10','path':'/资源/sub/说明.pdf'}]
            return self.reply({'errno':0,'share_id':77,'uk':88,'list':items})
        if not signed:return self.reply({'errno':-6})
        if url.path=='/api/gettemplatevariable':return self.reply({'errno':0,'result':{'bdstoken':'bdstoken','username':'百度用户'}})
        if url.path=='/api/list':return self.reply({'errno':0 if self.baidu_folder else -9,'list':[]})
        if url.path=='/api/create':
            assert form['path']=='/Plexus转存' and query['bdstoken']=='bdstoken'
            self.baidu_folder = True;return self.reply({'errno':0})
        if url.path=='/share/transfer':
            assert (query['shareid'],query['from'],query['sekey'],query['async'],form['path'])==('77','88','rk=','1','/Plexus转存')
            ids = json.loads(form['fsidlist']);self.saved['baidu'] = ids
            return self.reply({'errno':0,'extra':{'list':[{'from_fs_id':i,'to':'/Plexus转存/Inception.2010.1080p.mkv'} for i in ids]}})
        if url.path=='/api/filemetas':
            return self.reply({'errno':0,'info':[{'path':p,'dlink':'https://d.pcs.baidu.com/file/'+str(n)} for n,p in enumerate(json.loads(query['target']))]})
        return self.reply({'errno':404})


def wait(condition,label,seconds=5):
    deadline = time.time()+seconds
    while time.time()<deadline:
        if condition():return
        time.sleep(.01)
    raise AssertionError('Timed out: '+label)


class NetDiskTests(Fixture,unittest.TestCase):
    def setUp(self):
        # Sign-ins are shared through a store outside the library; each test gets its own.
        self.shared = tempfile.TemporaryDirectory()
        self.fake = FakeDrives()
        self.patches = [patch.dict(os.environ,{'COLLECTIONKIT_DIR':self.shared.name}),
                        patch('collectionkit.netdisk.fetch',side_effect=lambda request,timeout,sink=None:self.fake(request,timeout,sink)),
                        patch('collectionkit.netdisk.POLL',.01)]
        for item in self.patches:item.start()
        super().setUp()
        self.lib.netdisk = NetDisk(self.lib)
        self.lib.start_sync = lambda:True
        downloads = self.lib.downloads
        downloads.background = False
        downloads.staging_base = str(self.base/'staging')
        downloads._tell_plex = lambda section,folders:None

    def tearDown(self):
        super().tearDown()
        for item in reversed(self.patches):item.stop()
        self.shared.cleanup()

    def other_app(self):
        """书屿 on the same computer: its own drives, the same shared sign-ins."""
        return netdisk.Drives('书屿转存',Credentials(self.shared.name))

    def sign_in(self,key):
        status = self.lib.netdisk.drive(key).login()
        wait(lambda:self.lib.netdisk.status()[key]['signed_in'],key+' signed in')
        return status

    def download(self,text,location):
        share = self.lib.downloads.resolve(text)['shares'][0]
        batch_id = self.lib.downloads.add(share['token'],[f['index'] for f in share['files'] if f['kind']],str(location))['id']
        for _ in range(4):
            self.lib.downloads.retry_at.clear()
            self.lib.downloads.tick()
        return share,self.lib.downloads.public(batch_id)

    def test_share_links_and_extraction_codes_found_in_pasted_text(self):
        text = ('我用夸克网盘分享了「电影」，点击链接即可保存。\n链接：https://pan.quark.cn/s/34d748b55cc7 提取码：AB12\n'
                '链接: https://pan.baidu.com/s/1UgC33lOxfa_dhSr6beLnoA?pwd=4g8j 提取码: 4g8j 复制这段内容后打开百度网盘手机App\n'
                'https://pan.baidu.com/share/init?surl=abcDEF https://pan.quark.cn/s/abc')
        self.assertEqual(links(text),[{'drive':'quark','id':'34d748b55cc7','code':'AB12'},{'drive':'baidu','id':'UgC33lOxfa_dhSr6beLnoA','code':'4g8j'},
                                      {'drive':'baidu','id':'abcDEF','code':''},{'drive':'quark','id':'abc','code':''}])
        self.assertEqual(links('没有链接'),[])
        self.assertEqual((wanted('a.MKV',10),wanted('a.ass',10),wanted('a.jpg',10),wanted('a.mp4',0)),('video','subtitle','',''))

    def test_quark_sign_in_then_an_episode_resumes_and_lands_under_its_plex_name(self):
        status = self.sign_in('quark')
        self.assertEqual(status['login']['state'],'waiting');self.assertTrue(status['login']['qr']);self.assertIn('token=token-1',status['login']['link'])
        self.assertFalse(any(k.startswith('_') for k in status['login']),'tokens stay on the backend')
        self.assertEqual(self.fake.quark_polls,2,'an expired QR code is replaced')
        self.assertEqual(self.lib.netdisk.status()['quark']['name'],'夸克用户')
        self.assertNotIn('pus-cookie',json.dumps(self.lib.netdisk.drive('quark').session.values()),'cookies are stored protected')
        self.assertTrue(NetDisk(self.lib).drive('quark').signed_in,'the sign-in survives a restart')

        share,batch = self.download('链接：https://pan.quark.cn/s/qshare',self.shows)
        self.assertEqual(share['title'],'某剧')
        self.assertEqual({f['path']:f['kind'] for f in share['files']}['封面.jpg'],'')
        self.assertEqual(batch['state'],'review',batch)
        self.assertEqual((self.fake.saved['quark']['fid_list'],self.fake.saved['quark']['pdir_fid']),(['f1','f2'],'d1'),'saved from its own folder')
        ranges = [c['start'] for c in self.fake.calls if c['host']=='cdn.quark.test' and c['path']=='/s-f1']
        self.assertEqual(ranges,[0,300],'the second request continues where the first stopped')
        self.lib.downloads.apply(batch['id'])
        season = self.shows/'某剧 (2024)'/'Season 01'
        self.assertEqual((season/'某剧 (2024) - S01E01.mkv').read_bytes(),EPISODE)
        self.assertEqual((season/'某剧 (2024) - S01E01.chs.ass').read_bytes(),SUBTITLE)

        missing = self.lib.downloads.resolve('https://pan.quark.cn/s/gone')['shares'][0]
        self.assertEqual(missing['error'],'夸克网盘：分享不存在')

    def test_baidu_scan_then_extraction_code_checked_and_movie_downloaded(self):
        status = self.sign_in('baidu')
        self.assertTrue(status['login']['image'].startswith('data:image/png;base64,'),'the QR image is passed on as data')
        self.assertEqual(self.lib.netdisk.status()['baidu']['name'],'百度用户')
        wrong = self.lib.downloads.resolve('https://pan.baidu.com/s/1bshare?pwd=zzzz')['shares'][0]
        self.assertEqual(wrong['error'],'百度网盘：提取码不对，或者分享已失效。')
        share,batch = self.download('链接: https://pan.baidu.com/s/1bshare 提取码: abcd',self.movies)
        self.assertEqual(self.fake.saved['baidu'],[11])
        self.assertEqual(batch['state'],'review',batch)
        self.lib.downloads.apply(batch['id'])
        self.assertEqual((self.movies/'Inception (2010)'/'Inception (2010).mkv').read_bytes(),MOVIE)

    def test_sign_in_needed_first_and_again_when_it_lapses(self):
        with self.assertRaises(NetDiskError) as caught:self.lib.downloads.resolve('https://pan.baidu.com/s/1bshare 提取码 abcd')
        self.assertEqual(caught.exception.login,'baidu')
        with self.assertRaises(NetDiskError):self.lib.downloads.resolve('随便一段话')
        self.sign_in('quark')
        self.fake.quark_logged_in = False
        with self.assertRaises(NetDiskError) as caught:self.lib.downloads.resolve('https://pan.quark.cn/s/qshare')
        self.assertEqual(caught.exception.login,'quark');self.assertIn('重新扫码',str(caught.exception))
        self.assertFalse(self.lib.netdisk.status()['quark']['signed_in'])
        self.sign_in('quark')
        self.assertEqual(self.lib.netdisk.drive('quark').logout()['signed_in'],False)
        self.assertFalse(NetDisk(self.lib).drive('quark').signed_in)

    def test_a_sign_in_saved_before_sharing_moves_to_the_shared_store_once(self):
        cookies = [{'name':'__pus','value':'pus-cookie','domain':'.quark.cn','path':'/','secure':False,'expires':None}]
        with self.lib.db() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('netdisk_quark',json.dumps({'name':'旧登录','cookies':protect_secret(json.dumps(cookies))})))
        moved = NetDisk(self.lib)
        self.assertTrue(moved.drive('quark').signed_in);self.assertEqual(moved.status()['quark']['name'],'旧登录')
        self.assertTrue(self.other_app().drive('quark').signed_in,'书屿 has it too')
        with self.lib.db() as db:self.assertTrue(db.execute("SELECT 1 FROM settings WHERE key='netdisk_quark'").fetchone(),'the old row stays for going back')
        moved.drive('quark').logout()
        self.assertFalse(NetDisk(self.lib).drive('quark').signed_in,'a sign-out is not undone by the old row')

    def test_signing_in_or_out_in_another_app_shows_here(self):
        shuyu = self.other_app()
        shuyu.drive('quark').login()
        wait(lambda:shuyu.status()['quark']['signed_in'],'signed in from 书屿')
        self.assertEqual(self.lib.netdisk.status()['quark']['name'],'夸克用户')
        share,batch = self.download('https://pan.quark.cn/s/qshare',self.shows)
        self.assertEqual(batch['state'],'review',batch)
        self.lib.netdisk.drive('quark').logout()
        self.assertFalse(shuyu.status()['quark']['signed_in'],'signing out here signs 书屿 out too')

    def test_a_download_waits_for_a_new_sign_in_and_resumes_where_it_stopped(self):
        self.sign_in('quark')
        shuyu = self.other_app()
        share = self.lib.downloads.resolve('https://pan.quark.cn/s/qshare')['shares'][0]
        batch_id = self.lib.downloads.add(share['token'],[f['index'] for f in share['files'] if f['kind']],str(self.shows))['id']
        downloads = self.lib.downloads
        downloads.tick()  # Saved into your drive.
        downloads.tick()  # The episode stops 300 bytes in.
        # 书屿 signs out meanwhile: nothing new starts, and nothing is called failed.
        shuyu.drive('quark').logout()
        for _ in range(3):downloads.retry_at.clear();downloads.tick()
        batch = downloads.public(batch_id)
        self.assertEqual((batch['state'],batch['login']),('downloading','quark'),batch)
        self.assertNotIn('failed',[f['state'] for f in batch['files']])
        # Signed in again in 书屿, but the drive has dropped that sign-in by the time the download asks for its link.
        shuyu.drive('quark').login();wait(lambda:shuyu.status()['quark']['signed_in'],'signed in again from 书屿')
        self.fake.quark_logged_in = False
        downloads.retry_at.clear();downloads.tick()
        batch = downloads.public(batch_id)
        episode = next(f for f in batch['files'] if f['path'].endswith('.mkv'))
        self.assertEqual((batch['state'],batch['login'],episode['state']),('downloading','quark','ready'),batch)
        self.assertIn('断点',episode['error'])
        self.assertFalse(shuyu.status()['quark']['signed_in'],'the lapsed sign-in is dropped for every app')
        # Signing in again here picks it up from where it stopped.
        self.fake.quark_logged_in = True
        self.sign_in('quark')
        for _ in range(3):downloads.retry_at.clear();downloads.tick()
        batch = downloads.public(batch_id)
        self.assertEqual((batch['state'],batch['login']),('review',''),batch)
        ranges = [c['start'] for c in self.fake.calls if c['host']=='cdn.quark.test' and c['path']=='/s-f1']
        self.assertEqual(ranges,[0,300],'the episode continues from the 300 bytes it had')

    def test_saving_waits_for_a_sign_in_instead_of_failing(self):
        self.sign_in('quark')
        share = self.lib.downloads.resolve('https://pan.quark.cn/s/qshare')['shares'][0]
        batch_id = self.lib.downloads.add(share['token'],[f['index'] for f in share['files'] if f['kind']],str(self.shows))['id']
        self.fake.quark_logged_in = False
        self.lib.downloads.tick()
        batch = self.lib.downloads.public(batch_id)
        self.assertEqual((batch['state'],batch['login']),('queued','quark'),batch);self.assertIn('重新登录后会从断点继续',batch['error'])
        self.fake.quark_logged_in = True
        self.sign_in('quark')
        for _ in range(4):self.lib.downloads.retry_at.clear();self.lib.downloads.tick()
        self.assertEqual(self.lib.downloads.public(batch_id)['state'],'review')


if __name__=='__main__':
    unittest.main()
