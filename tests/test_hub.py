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


if __name__=='__main__':
    unittest.main()
