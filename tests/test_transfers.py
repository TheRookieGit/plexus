import sqlite3
import time
import unittest
from unittest.mock import patch

from netdisk import NetDiskError
from tests.test_catalog import Fixture
from transfers import Transfers, folder_name, suggested_folder


class FakeDrive:
    key,label,app = 'quark','夸克网盘','夸克 App'
    signed_in = True

    def __init__(self,content):
        self.content = content       # share path -> size
        self.saved = []
        self.folders = []
        self.refused = set()         # paths the drive reports as not saved
        self.break_after = None      # files to save before the connection drops, once
        self.read = 0

    def status(self):
        return {'label':self.label,'signed_in':True}

    def files(self,link):
        self.read += 1
        return {'id':link['id'],'title':'','stoken':f'token-{self.read}'},[{'name':p.rsplit('/',1)[-1],'path':p,'size':s,'_fid':p} for p,s in self.content.items()]

    def save(self,share,files,folder=''):
        self.folders.append(folder)
        for item in files:
            if self.break_after==len(self.saved):
                self.break_after = None
                raise NetDiskError('网盘暂时不可用。')
            if item['path'] in self.refused:
                yield item,'没有转存成功'
                continue
            self.saved.append(item['path'])
            yield item,{'fid':item['_fid']}


class TransfersTest(Fixture,unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.content = {'某新剧.2023/某新剧.S01E01.mkv':30,'某新剧.2023/某新剧.S01E02.mkv':30,'某新剧.2023/某新剧.S01E01.chs.ass':4,'某新剧.2023/海报.jpg':3}
        self.drive = FakeDrive(self.content)
        self.lib.netdisk.drives['quark'] = self.drive
        self.transfers = self.lib.transfers
        self.transfers.background = False

    def queue(self):
        share = self.transfers.resolve('https://pan.quark.cn/s/abc 提取码 1234')['shares'][0]
        self.assertEqual({f['kind'] for f in share['files']},{'video','subtitle',''})
        return self.transfers.add(share['token'],[f['index'] for f in share['files'] if f['kind']])['id']

    def test_chosen_files_are_saved_and_nothing_private_reaches_the_page(self):
        transfer_id = self.queue()
        self.assertEqual(self.lib.status()['transfers']['active'],1)
        self.transfers.tick()
        shown = self.transfers.public(transfer_id)
        self.assertEqual(shown['state'],'done')
        self.assertEqual(len(self.drive.saved),3)
        self.assertNotIn('某新剧.2023/海报.jpg',self.drive.saved)
        self.assertEqual(shown['size'],64)
        self.assertEqual(self.lib.status()['transfers']['active'],0)
        self.assertFalse(any(k.startswith('_') for f in shown['files'] for k in f))
        self.assertNotIn('share',shown)

    def test_each_share_gets_its_own_dated_folder_which_can_be_renamed(self):
        with patch('transfers.time.localtime',return_value=time.struct_time((2026,11,6,12,0,0,4,310,0))):
            self.assertEqual(suggested_folder('潜伏.2008.4K修复.国语.中字[30集已完结]'),'11.6.2026 潜伏')
            self.assertEqual(self.transfers.resolve('https://pan.quark.cn/s/abc')['shares'][0]['folder'],'11.6.2026 某新剧')
            transfer_id = self.queue()
        self.transfers.tick()
        self.assertEqual(self.drive.folders,['11.6.2026 某新剧'])
        self.assertEqual(self.transfers.public(transfer_id)['folder'],'Plexus转存/11.6.2026 某新剧')
        share = self.transfers.resolve('https://pan.quark.cn/s/abc')['shares'][0]
        renamed = self.transfers.add(share['token'],[0],'  菜鸟老警/第8季?  ')['id']
        self.transfers.tick()
        self.assertEqual(self.drive.folders[-1],'菜鸟老警 第8季')
        self.assertEqual(self.transfers.public(renamed)['folder'],'Plexus转存/菜鸟老警 第8季')
        self.assertEqual(folder_name('.. / ..'),'')

    def test_a_retry_goes_to_the_same_folder_and_a_table_from_the_last_version_gains_the_column(self):
        with self.lib.db() as db:
            db.execute('DROP TABLE transfers')
            db.execute('''CREATE TABLE transfers (id TEXT PRIMARY KEY,created REAL NOT NULL,drive TEXT NOT NULL,link TEXT NOT NULL,title TEXT NOT NULL,
                          state TEXT NOT NULL,error TEXT NOT NULL,share TEXT NOT NULL,files TEXT NOT NULL,finished REAL)''')
            db.execute("INSERT INTO transfers VALUES ('old',1,'quark','{}','旧','done','','{}','[]',2)")
        self.transfers = Transfers(self.lib)
        self.transfers.background = False
        self.assertEqual(self.transfers.public('old')['folder'],'Plexus转存')
        self.drive.refused.add(next(iter(self.content)))
        transfer_id = self.queue()
        self.transfers.tick()
        self.drive.refused.clear()
        self.transfers.control({'id':transfer_id,'action':'retry'})
        self.transfers.tick()
        self.assertEqual(len(set(self.drive.folders)),1)
        self.assertEqual(len(self.drive.folders),2)

    def test_a_file_the_drive_refuses_can_be_retried_alone(self):
        refused = next(iter(self.content))
        self.drive.refused.add(refused)
        transfer_id = self.queue()
        self.transfers.tick()
        shown = self.transfers.public(transfer_id)
        self.assertEqual(shown['state'],'failed')
        self.assertEqual(shown['error'],'1 个文件没能转存：没有转存成功')
        self.drive.refused.clear()
        self.transfers.control({'id':transfer_id,'action':'retry'})
        self.transfers.tick()
        self.assertEqual(self.transfers.public(transfer_id)['state'],'done')
        self.assertEqual(self.drive.saved.count(refused),1)
        self.assertEqual(len(self.drive.saved),3,'the others are not saved twice')

    def test_a_failure_part_way_reads_the_share_again_and_repeats_nothing(self):
        self.drive.break_after = 1
        transfer_id = self.queue()
        self.transfers.tick()
        self.assertEqual(self.transfers.public(transfer_id)['state'],'done')
        self.assertEqual(self.drive.read,2)
        self.assertEqual(len(self.drive.saved),3)
        self.assertEqual(len(set(self.drive.saved)),3)
        self.assertEqual(self.transfers.transfer(transfer_id)['share']['stoken'],'token-2')

    def test_only_the_record_is_removed(self):
        transfer_id = self.queue()
        self.transfers.tick()
        self.transfers.control({'id':transfer_id,'action':'remove'})
        self.assertEqual(self.transfers.listing()['transfers'],[])
        self.assertEqual(len(self.drive.saved),3)

    def test_expired_reading_and_empty_choice_are_refused(self):
        with self.assertRaises(ValueError):self.transfers.add('gone',[0])
        share = self.transfers.resolve('https://pan.quark.cn/s/abc')['shares'][0]
        with self.assertRaises(ValueError):self.transfers.add(share['token'],[])

    def test_an_interrupted_save_starts_again_and_old_download_tables_go(self):
        transfer_id = self.queue()
        self.transfers.update(transfer_id,state='saving')
        with self.lib.db() as db:db.execute('CREATE TABLE dl_batches (id TEXT)')
        again = Transfers(self.lib)
        self.assertEqual(again.transfer(transfer_id)['state'],'queued')
        with self.lib.db() as db:
            with self.assertRaises(sqlite3.OperationalError):db.execute('SELECT 1 FROM dl_batches')


if __name__=='__main__':
    unittest.main()
