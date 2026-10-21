from pathlib import Path
import unittest

from naming import episode_of, plan
from netdisk import Unreachable
from tests.test_catalog import Fixture


class FakeDrive:
    key,label,app = 'quark','夸克网盘','夸克 App'
    signed_in = True

    def __init__(self,content):
        self.content = content       # share path -> bytes
        self.breaks = {}             # share path -> bytes to send before the connection drops, once
        self.ignore_range = set()    # share paths whose server always sends the whole file
        self.saved = []

    def status(self):
        return {'label':self.label,'signed_in':True}

    def files(self,link):
        return {'id':link['id'],'title':''},[{'name':p.rsplit('/',1)[-1],'path':p,'size':len(b),'_fid':p} for p,b in self.content.items()]

    def save(self,share,files):
        for item in files:
            self.saved.append(item['path'])
            yield item,{'fid':item['_fid']}

    def stream(self,ref,start,sink):
        data = self.content[ref['fid']]
        whole = ref['fid'] in self.ignore_range
        sent = data if whole else data[start:]
        cut = self.breaks.pop(ref['fid'],None)
        if cut is not None:
            sink.write(sent[:cut])
            raise Unreachable('下载中断了，会自动重试。')
        for i in range(0,len(sent),4):sink.write(sent[i:i+4])
        return 200 if whole or not start else 206


class DownloadsTest(Fixture,unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.content = {'某新剧.2023/某新剧.S01E01.1080p.mkv':b'episode one'*3,'某新剧.2023/某新剧.S01E02.1080p.mkv':b'episode two'*3,
                        '某新剧.2023/某新剧.S01E01.1080p.chs.ass':b'subs','某新剧.2023/海报.jpg':b'jpg'}
        self.drive = FakeDrive(self.content)
        self.lib.netdisk.drives['quark'] = self.drive
        self.lib.start_sync = lambda:True
        downloads = self.lib.downloads
        downloads.background = False
        downloads.staging_base = str(self.base/'staging')
        downloads._tell_plex = lambda section,folders:None

    def queue(self):
        resolved = self.lib.downloads.resolve('https://pan.quark.cn/s/abc 提取码 1234')
        share = resolved['shares'][0]
        self.assertEqual({f['kind'] for f in share['files']},{'video','subtitle',''})
        self.assertEqual(share['guess'],{'title':'某新剧','year':2023,'kind':'show'})
        chosen = [f['index'] for f in share['files'] if f['kind']]
        return self.lib.downloads.add(share['token'],chosen,str(self.shows))['id']

    def run_queue(self,times=4):
        for _ in range(times):
            self.lib.downloads.retry_at.clear()
            self.lib.downloads.tick()

    def test_download_review_and_move_into_the_library(self):
        batch_id = self.queue()
        self.run_queue()
        batch = self.lib.downloads.public(batch_id)
        self.assertEqual(batch['state'],'review')
        self.assertEqual(len(self.drive.saved),3)
        targets = {Path(p['path']).name:p['target'] for p in batch['plan']}
        show = self.shows/'某新剧 (2023)'/'Season 01'
        self.assertEqual(targets['某新剧.S01E01.1080p.mkv'],str(show/'某新剧 (2023) - S01E01.mkv'))
        self.assertEqual(targets['某新剧.S01E01.1080p.chs.ass'],str(show/'某新剧 (2023) - S01E01.chs.ass'))
        # Nothing reaches the library before the names are confirmed.
        self.assertFalse(show.exists())
        self.lib.downloads.apply(batch_id)
        self.assertEqual((show/'某新剧 (2023) - S01E02.mkv').read_bytes(),b'episode two'*3)
        self.assertEqual(self.lib.downloads.public(batch_id)['state'],'done')
        self.assertFalse(Path(self.lib.downloads.batch_folder({'id':batch_id,'location':str(self.shows)})).exists())

    def test_resumes_after_a_dropped_connection(self):
        self.drive.breaks['某新剧.2023/某新剧.S01E01.1080p.mkv'] = 10
        batch_id = self.queue()
        self.lib.downloads.tick()  # Saves into your drive.
        self.lib.downloads.tick()  # Downloads, until the connection drops.
        files = {Path(f['path']).name:f for f in self.lib.downloads.public(batch_id)['files']}
        first = files['某新剧.S01E01.1080p.mkv']
        self.assertEqual((first['state'],first['attempts']),('ready',1))
        self.run_queue()
        batch = self.lib.downloads.public(batch_id)
        self.assertEqual(batch['state'],'review')
        self.lib.downloads.apply(batch_id)
        self.assertEqual((self.shows/'某新剧 (2023)'/'Season 01'/'某新剧 (2023) - S01E01.mkv').read_bytes(),b'episode one'*3)

    def test_server_that_ignores_the_range_starts_over(self):
        name = '某新剧.2023/某新剧.S01E02.1080p.mkv'
        self.drive.breaks[name] = 7
        self.drive.ignore_range.add(name)
        batch_id = self.queue()
        self.run_queue()
        self.lib.downloads.apply(batch_id)
        self.assertEqual((self.shows/'某新剧 (2023)'/'Season 01'/'某新剧 (2023) - S01E02.mkv').read_bytes(),b'episode two'*3)

    def test_change_what_it_is_before_moving(self):
        batch_id = self.queue()
        self.run_queue()
        batch = self.lib.downloads.public(batch_id)
        episode = next(f for f in batch['files'] if f['path'].endswith('S01E02.1080p.mkv'))
        self.lib.downloads.identify({'id':batch_id,'title':'另一个名字','year':'2024','kind':'show','files':[{'id':episode['id'],'season':2,'episode':5}]})
        targets = {Path(p['path']).name:p['target'] for p in self.lib.downloads.public(batch_id)['plan']}
        self.assertTrue(targets['某新剧.S01E02.1080p.mkv'].endswith('另一个名字 (2024)\\Season 02\\另一个名字 (2024) - S02E05.mkv'))

    def test_keep_names_as_shared(self):
        batch_id = self.queue()
        self.run_queue()
        self.lib.downloads.apply(batch_id,rename=False)
        self.assertTrue((self.shows/'某新剧.2023'/'某新剧.S01E01.1080p.mkv').exists())

    def test_pause_resume_and_cancel(self):
        batch_id = self.queue()
        self.lib.downloads._save(batch_id)
        self.lib.downloads.control({'batch':batch_id,'action':'pause'})
        self.lib.downloads.tick()
        self.assertTrue(all(f['state']=='paused' for f in self.lib.downloads.public(batch_id)['files']))
        self.lib.downloads.control({'batch':batch_id,'action':'resume'})
        self.run_queue()
        self.assertEqual(self.lib.downloads.public(batch_id)['state'],'review')
        self.lib.downloads.control({'batch':batch_id,'action':'cancel'})
        self.assertEqual(self.lib.downloads.public(batch_id)['state'],'canceled')
        self.lib.downloads.control({'batch':batch_id,'action':'remove'})
        self.assertEqual(self.lib.downloads.listing()['batches'],[])

    def test_destination_must_be_a_library_folder(self):
        resolved = self.lib.downloads.resolve('https://pan.quark.cn/s/abc')
        with self.assertRaises(ValueError):
            self.lib.downloads.add(resolved['shares'][0]['token'],[0],str(self.base))


class NamingTest(unittest.TestCase):
    def test_movie_with_subtitles_and_parts(self):
        files = [{'id':'a','path':'Inception.2010/Inception.2010.1080p.mkv','size':10},{'id':'b','path':'Inception.2010/Inception.2010.1080p.zh.srt','size':1}]
        result = {f['id']:f['target'] for f in plan(files,'movie','盗梦空间',2010,'E:\\电影')}
        self.assertEqual(result,{'a':'E:\\电影\\盗梦空间 (2010)\\盗梦空间 (2010).mkv','b':'E:\\电影\\盗梦空间 (2010)\\盗梦空间 (2010).zh.srt'})
        parts = [{'id':'1','path':'Movie CD1.avi','size':5},{'id':'2','path':'Movie CD2.avi','size':5}]
        self.assertEqual(sorted(f['target'] for f in plan(parts,'movie','片名',None,'E:\\电影')),['E:\\电影\\片名\\片名 - pt1.avi','E:\\电影\\片名\\片名 - pt2.avi'])

    def test_unsafe_characters(self):
        result = plan([{'id':'a','path':'x.mkv','size':1}],'movie','Mission: Impossible?',1996,'E:\\电影')
        self.assertEqual(result[0]['target'],'E:\\电影\\Mission： Impossible？ (1996)\\Mission： Impossible？ (1996).mkv')

    def test_show_joins_existing_folder(self):
        result = plan([{'id':'a','path':'第3集.mp4','size':1}],'show','某剧',2020,'E:\\剧','E:\\剧\\某剧.2020.4K')
        self.assertEqual(result[0]['target'],'E:\\剧\\某剧.2020.4K\\Season 01\\某剧 (2020) - S01E03.mp4')

    def test_episode_numbers(self):
        self.assertEqual(episode_of('Show.S02E10.mkv'),(2,10))
        self.assertEqual(episode_of('[字幕组] 名字 - 07 [1080p].mkv'),(None,7))
        self.assertEqual(episode_of('Movie.2019.1080p.mkv'),(None,None))


if __name__=='__main__':
    unittest.main()
