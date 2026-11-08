import tempfile
import unittest
from unittest.mock import patch
import urllib.parse

from collectionkit.hub import Hub
from collectionkit.testing import FakeHub
from tests.test_catalog import Fixture
from wishlist import WEB


def wish(uid,title,category='movie',year=None,**extra):
    return {'id':uid,'category':category,'title':title,'extra':{'year':str(year)} if year else {},'links':[],
            'createdAt':'2026-01-01T00:00:00Z','updatedAt':'2026-01-01T00:00:00Z','done':False,**extra}


class HubWishlistTests(Fixture,unittest.TestCase):
    """The wishlist connected to the wish hub, with the hub in memory."""
    def setUp(self):
        super().setUp()
        self.shared = tempfile.TemporaryDirectory()
        self.fake = FakeHub()
        self.patch = patch('collectionkit.hub.fetch',side_effect=self.fake)
        self.patch.start()
        self.wishes = self.lib.wishlist
        self.wishes.hub = Hub('plexus',self.shared.name)

    def tearDown(self):
        self.patch.stop()
        self.shared.cleanup()
        super().tearDown()

    def owned(self,uid):
        return self.fake.fulfilled.get((uid,'plexus'))

    def listed(self):
        return {w['title']:w for w in self.wishes.items()['items']}

    def test_connecting_uploads_the_local_wishlist_once_and_reports_what_plex_has(self):
        self.wishes.save({'title':'盗梦空间','year':'2010','kind':'movie','links':'链接：https://pan.quark.cn/s/abc\n提取码：1234'})
        self.wishes.save({'title':'还没有的片','kind':'show','guid':'plex://show/new','thumb':'https://img/x.jpg','summary':'简介'})
        self.fake.add(wish('h1','星际穿越',year=2014))
        self.fake.add(wish('h2','盗梦空间',year=2010))
        self.fake.add({'id':'n1','category':'novel','title':'小说'})
        self.wishes.pair(self.fake.token('plexus'))
        sent = [i for i,_ in self.fake.items.values() if i['category'] in ('movie','tv')]
        self.assertEqual(sorted(i['title'] for i in sent),['星际穿越','盗梦空间','还没有的片'],'盗梦空间 (2010) was in the hub already')
        show = next(i for i in sent if i['title']=='还没有的片')
        self.assertEqual((show['category'],show['externalIds'],show['thumb'],show['summary'],show['source']),('tv',{'plexGuid':'plex://show/new'},'https://img/x.jpg','简介','plexus'))
        listed = self.listed()
        self.assertTrue(listed['星际穿越']['owned'] and listed['盗梦空间']['owned'] and not listed['还没有的片']['owned'])
        self.assertEqual(listed['还没有的片']['kind'],'show')
        interstellar = self.owned('h1')
        self.assertEqual(interstellar['ref'],'plex://movie/x','owned by the same Plex title')
        self.assertEqual(interstellar['url'],WEB+'#item='+urllib.parse.quote(listed['星际穿越']['owned']))
        self.assertTrue(self.owned('h2'))
        calls = len(self.fake.calls)
        self.wishes.report()
        self.assertEqual(len(self.fake.calls),calls,'reported once')
        with self.lib.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM wishlist').fetchone()[0],2,'the local list stays as it was')
        self.wishes.unpair()
        self.assertEqual(set(self.listed()),{'盗梦空间','还没有的片'},'back to the local list')

    def test_share_text_goes_whole_and_comes_back_whole(self):
        self.wishes.pair(self.fake.token('plexus'))
        text = '链接：https://pan.quark.cn/s/abc\n提取码：1234'
        added = self.wishes.save({'title':'新片','year':'2025','kind':'movie','links':text})
        self.assertEqual(self.fake.items[added['id']][0]['links'],[text],'the code stays with its link')
        self.assertEqual(self.listed()['新片']['links'],text)
        self.fake.add(wish('h3','网页上加的',category='tv',links=['https://pan.baidu.com/s/1a','https://pan.quark.cn/s/b'],updatedAt='2026-05-01T00:00:00Z'))
        self.wishes.pull()
        self.assertEqual(self.listed()['网页上加的']['links'],'https://pan.baidu.com/s/1a\nhttps://pan.quark.cn/s/b')

    def test_each_link_stays_its_own_entry_in_the_hub_after_saving_here(self):
        self.wishes.pair(self.fake.token('plexus'))
        a,b = 'https://pan.baidu.com/s/1a','https://pan.quark.cn/s/b'
        self.fake.add(wish('h4','两个链接',links=[a,b],updatedAt='2026-05-02T00:00:00Z'))
        self.wishes.pull()
        row = self.listed()['两个链接']
        self.wishes.save({'id':'h4','title':'两个链接','kind':'movie','links':row['links'],'note':'改了备注'})
        self.assertEqual(self.fake.items['h4'][0]['links'],[a,b])

    def test_links_changed_on_both_sides_at_once_lose_neither_change(self):
        self.wishes.pair(self.fake.token('plexus'))
        a,b,c = 'https://pan.quark.cn/s/a','https://pan.quark.cn/s/b','https://pan.quark.cn/s/c'
        d = 'https://pan.quark.cn/s/d?pwd=1234'
        self.fake.add(wish('h5','两边都改',links=[a,b],updatedAt='2026-05-02T00:00:00Z'))
        self.wishes.pull()
        # The form opened here shows a and b. Meanwhile the hub adds c, and here a is removed and d added.
        self.fake.add(wish('h5','两边都改',links=[a,b,c],updatedAt='2026-05-03T00:00:00Z'))
        self.wishes.save({'id':'h5','title':'两边都改','kind':'movie','links':f'{b}\n{d}'})
        self.assertEqual(self.fake.items['h5'][0]['links'],[b,c,d])
        self.assertEqual(self.listed()['两边都改']['links'],f'{b}\n{c}\n{d}')

    def test_links_removed_in_the_hub_go_here_too(self):
        self.wishes.pair(self.fake.token('plexus'))
        a,b = 'https://pan.quark.cn/s/a','https://pan.quark.cn/s/b'
        self.fake.add(wish('h6','中枢删链接',links=[a,b],updatedAt='2026-05-02T00:00:00Z'))
        self.wishes.pull()
        self.fake.add(wish('h6','中枢删链接',links=[b],updatedAt='2026-05-03T00:00:00Z'))
        self.wishes.pull()
        self.assertEqual(self.listed()['中枢删链接']['links'],b)

    def test_a_rejected_match_is_passed_over_and_a_title_gone_from_plex_is_owned_no_more(self):
        self.fake.add(wish('h1','星际穿越',year=2014))
        self.wishes.pair(self.fake.token('plexus'))
        self.assertTrue(self.listed()['星际穿越']['owned'])
        self.fake.reject('h1','plexus')
        self.wishes.pull()
        self.assertFalse(self.listed()['星际穿越']['owned'],'不是这个')
        self.assertTrue(self.fake.fulfilled[('h1','plexus')]['rejected'],'nothing sent that would lift the rejection')
        self.fake.add(wish('h2','盗梦空间',year=2010,updatedAt='2026-05-01T00:00:00Z'))
        self.wishes.pull()
        self.assertTrue(self.owned('h2')['ownedAt'])
        with self.lib.db() as db:db.execute("UPDATE items SET files=0 WHERE title='盗梦空间'")
        self.wishes.refresh()
        self.assertIsNone(self.owned('h2')['ownedAt'],'gone from Plex')

    def test_offline_the_wishlist_works_and_catches_up_later(self):
        self.wishes.pair(self.fake.token('plexus'))
        self.fake.offline = True
        added = self.wishes.save({'title':'离线时加的','kind':'movie'})
        self.assertIn('离线时加的',self.listed())
        self.assertFalse(self.wishes.pull())
        self.assertEqual(self.wishes.hub_status()['queued'],1)
        self.fake.offline = False
        self.assertTrue(self.wishes.pull())
        self.assertIn(added['id'],self.fake.items)
        self.wishes.delete(added['id'])
        self.assertTrue(self.fake.items[added['id']][0]['deleted'])


    def test_screenshots_come_from_the_hub_and_go_back_with_the_wish(self):
        self.fake.images['shot-hub-1'] = 'data:image/png;base64,iVBO'
        self.fake.add(wish('h1','截图里的片',shots=['shot-hub-1'],rating=5))
        self.wishes.pair(self.fake.token('plexus'))
        self.assertEqual(self.listed()['截图里的片']['shots'],['shot-hub-1'])
        self.assertEqual(self.wishes.shot('shot-hub-1')['data'],'data:image/png;base64,iVBO')
        added = self.wishes.add_shot('data:image/jpeg;base64,/9j/')['id']
        self.wishes.save({'id':'h1','title':'截图里的片','kind':'movie','shots':['shot-hub-1',added]})
        sent = self.fake.items['h1'][0]
        self.assertEqual(sent['shots'],['shot-hub-1',added]);self.assertEqual(sent['rating'],5)
        self.assertEqual(self.fake.images[added],'data:image/jpeg;base64,/9j/')
        self.wishes.save({'id':'h1','title':'截图里的片','kind':'movie','shots':[added]})
        self.assertEqual(self.fake.items['h1'][0]['shots'],[added],'removed here, removed in the hub')
        self.wishes.save({'id':'h1','title':'改个名','kind':'movie'})
        self.assertEqual(self.fake.items['h1'][0]['shots'],[added],'saving without shots leaves them alone')
        self.wishes.unpair()
        with self.assertRaises(ValueError):self.wishes.add_shot('data:image/jpeg;base64,/9j/')

    def test_wishes_from_the_hub_are_matched_to_plex_and_the_match_goes_back(self):
        catalog = {'黑洞频率':[{'title':'黑洞频率','original_title':'Frequency','year':2018,'kind':'show','guid':'plex://show/f','thumb':'https://img/show.jpg','summary':'剧'},
                               {'title':'黑洞频率','original_title':'Frequency','year':2000,'kind':'movie','guid':'plex://movie/f','thumb':'https://img/f.jpg','summary':'父子'}],
                   '致命女人':[{'title':'致命女人','original_title':'Why Women Kill','year':2019,'kind':'show','guid':'plex://show/w','thumb':'https://img/w.jpg','summary':'三个女人'}],
                   '来自深渊 剧场版':[{'title':'来自深渊：深魂的黎明','year':2020,'kind':'movie','guid':'plex://movie/a','thumb':'','summary':''}]}
        asked = []
        self.wishes.find = lambda title,kind:asked.append(title) or catalog.get(title,[])
        self.fake.add(wish('h1','黑洞频率',year=2000,summary='豆瓣8.4'))
        self.fake.add(wish('h2','致命女人',category='tv'))
        self.fake.add(wish('h3','来自深渊 剧场版'))
        self.wishes.pair(self.fake.token('plexus'))
        self.assertEqual(self.wishes.match(),2)
        listed = self.listed()
        self.assertEqual((listed['黑洞频率']['guid'],listed['黑洞频率']['thumb'],listed['黑洞频率']['summary']),('plex://movie/f','https://img/f.jpg','豆瓣8.4'),
                         'the year picks the movie, and the hub\'s own summary stays')
        self.assertEqual((listed['致命女人']['guid'],listed['致命女人']['year']),('plex://show/w',2019))
        self.assertEqual(listed['来自深渊 剧场版']['guid'],'','not clear enough to match')
        sent = self.fake.items['h1'][0]
        self.assertEqual((sent['externalIds']['plexGuid'],sent['thumb']),('plex://movie/f','https://img/f.jpg'))
        self.assertEqual(self.fake.items['h2'][0]['extra']['year'],'2019')
        asked.clear()
        self.assertEqual(self.wishes.match(),0)
        self.assertEqual(asked,[],'each title is searched once')
        self.wishes.save({'id':'h3','title':'黑洞频率','year':'2000','kind':'movie'})
        self.wishes.match()
        self.assertEqual(self.listed()['黑洞频率']['guid'],'plex://movie/f','renamed, so searched again')

    def test_a_wish_added_by_hand_is_matched_and_offline_waits_for_next_time(self):
        def offline(title,kind):raise OSError('offline')
        self.wishes.find = offline
        added = self.wishes.save({'title':'Noelle','year':'2019','kind':'movie'})
        self.assertEqual(self.wishes.match(),0)
        self.wishes.find = lambda title,kind:[{'title':'诺艾尔','original_title':'','year':2019,'kind':'movie','guid':'plex://movie/n','thumb':'https://img/n.jpg','summary':'圣诞'}]
        other = self.wishes.save({'title':'Noelle','year':'2008','kind':'movie'})
        self.assertEqual(self.wishes.match(),1,'Plex\'s first result under its Chinese title, as its year agrees; not the 2008 one')
        with self.lib.db() as db:self.assertEqual(db.execute('SELECT guid FROM wishlist WHERE id=?',(other['id'],)).fetchone()[0],'')
        with self.lib.db() as db:row = db.execute('SELECT guid,thumb,summary FROM wishlist WHERE id=?',(added['id'],)).fetchone()
        self.assertEqual(tuple(row),('plex://movie/n','https://img/n.jpg','圣诞'))

if __name__=='__main__':
    unittest.main()
