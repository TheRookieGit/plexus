from pathlib import Path
import tempfile
import unittest

from disk import guess_title
from server import Library


def movie(key,title,path,year=2020,resolution='1080'):
    return {'ratingKey':key,'title':title,'year':year,'thumb':f'/library/metadata/{key}/thumb/1','addedAt':key,
            'Media':[{'videoResolution':resolution,'videoCodec':'hevc','Part':[{'file':str(path),'size':10}]}]}


def episode(key,show,season,number,path):
    return {'ratingKey':key,'grandparentRatingKey':show,'parentIndex':season,'index':number,'title':f'Episode {number}',
            'Media':[{'videoResolution':'4k','Part':[{'file':str(path),'size':20}]}]}


class FakePlex:
    calls = []

    def __init__(self,sections,items):
        self._sections,self._items = sections,items

    def refresh(self,section,path=''):
        self.calls.append(('refresh',section,path))

    def wait_idle(self):
        pass

    def empty_trash(self,section):
        self.calls.append(('empty_trash',section))

    def server(self):
        return {'name':'test','version':'1.0','machine':'abc'}

    def sections(self):
        return self._sections

    def items(self,section,kind):
        return iter(self._items.get((section,kind),[]))


class Fixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.movies,self.shows,self.others = base/'电影',base/'剧集',base/'其他'
        self.base = base
        for path in ['电影/盗梦空间 (2010).mkv','电影/盗梦空间 (2010).chs.ass','电影/盗梦空间 (2010)-poster.jpg','电影/盗梦空间续集.nfo',
                     '电影/星际穿越 (2014)/星际穿越.1080p.mkv','电影/星际穿越 (2014)/星际穿越.2160p.mkv','电影/星际穿越 (2014)/fanart.jpg','电影/[无名之辈]Nobody.2018.1080p.mp4','电影/._盗梦空间 (2010).mkv',
                     '剧集/某剧/S01E01.mkv','剧集/某剧/S01E02.mkv','剧集/某剧/S01E03.mkv','剧集/某剧/S01E01.ass',
                     '剧集/新剧.2024.2160p/E01.mkv','其他/星际穿越.mkv']:
            (base/path).parent.mkdir(parents=True,exist_ok=True)
            (base/path).write_bytes(b'x'*5)
        sections = [{'id':'1','kind':'movie','title':'电影','locations':[str(self.movies),'Y:\\gone\\电影']},
                    {'id':'2','kind':'show','title':'剧集','locations':[str(self.shows)]},
                    {'id':'3','kind':'movie','title':'其他','locations':[str(self.others)]}]
        items = {('1','movie'):[movie(1,'盗梦空间',self.movies/'盗梦空间 (2010).mkv',2010,'4k'),movie(2,'消失的电影',self.movies/'消失 (2001).mkv'),
                                movie(3,'离线的电影','Y:\\gone\\电影\\离线.mkv'),
                                movie(4,'星际穿越',self.movies/'星际穿越 (2014)'/'星际穿越.1080p.mkv',2014)|{'guid':'plex://movie/x'}],
                 ('3','movie'):[movie(5,'星际穿越',self.others/'星际穿越.mkv',2014)|{'guid':'plex://movie/x'}],
                 ('2','show'):[{'ratingKey':10,'title':'某剧','thumb':'','addedAt':5}],
                 ('2','episode'):[episode(11,10,1,1,self.shows/'某剧'/'S01E01.mkv'),episode(12,10,1,2,self.shows/'某剧'/'S01E02.mkv')]}
        self.lib = Library(base/'data')
        self.lib.plex = lambda:FakePlex(sections,items)
        self.items = items
        self.lib.catalog.sync(self.lib.plex())

    def tearDown(self):
        self.temp.cleanup()


class CatalogTest(Fixture,unittest.TestCase):
    def entries(self,**query):
        return {i['title']:i for i in self.lib.catalog.listing(query)['items']}

    def test_plex_entries_are_matched_to_disk(self):
        entries = self.entries()
        self.assertEqual(entries['盗梦空间']['files'],1)
        self.assertEqual(entries['盗梦空间']['resolution'],'4k')
        self.assertEqual(entries['某剧']['episodes'],2)
        self.assertEqual(entries['某剧']['seasons'],1)

    def test_files_plex_does_not_list(self):
        entries = self.entries()
        # A third episode in a Plex show's folder joins that show.
        self.assertEqual(entries['某剧']['unindexed'],1)
        self.assertEqual(entries['某剧']['files'],3)
        # Anything else becomes its own entry, named from the file or folder.
        self.assertEqual(entries['无名之辈']['source'],'disk')
        self.assertEqual(entries['无名之辈']['year'],2018)
        self.assertEqual(entries['新剧']['kind'],'show')
        self.assertEqual(set(self.entries(source='disk')),{'无名之辈','新剧'})

    def test_missing_files_only_on_connected_folders(self):
        entries = self.entries(issue='missing')
        self.assertEqual(list(entries),['消失的电影'])
        offline = self.lib.catalog.item('plex:3')
        self.assertEqual(offline['media'][0]['state'],'offline')
        self.assertNotIn('离线的电影',self.entries())

    def test_hidden_mac_files_are_ignored(self):
        self.assertEqual(self.lib.catalog.stats()['files'],9)

    def test_subtitles_belong_to_the_entry(self):
        show = self.lib.catalog.item('plex:10')
        self.assertEqual([Path(s['path']).name for s in show['subtitles']],['S01E01.ass'])

    def test_tree_counts(self):
        root = self.lib.tree.node([])
        self.assertEqual(root['counts']['total'],9)
        self.assertEqual(root['counts']['unindexed'],4)
        path = [p for p in str(self.shows/'某剧').split('\\') if p]
        node = self.lib.tree.node(path)
        self.assertEqual(len(node['files']),3)
        self.assertEqual(sum(f['plex'] for f in node['files']),2)

    def test_versions_and_twins(self):
        # A second version Plex has not listed sits in the movie's own folder.
        self.items[('1','movie')][3]['Media'].append({'id':99,'videoResolution':'4k','Part':[{'file':str(self.movies/'星际穿越 (2014)'/'星际穿越.2160p.mkv'),'size':30}]})
        self.lib.catalog.sync(self.lib.plex())
        entry = self.lib.catalog.item('plex:4')
        self.assertEqual(entry['versions'],1)
        self.assertEqual(entry['spare'],10)
        self.assertEqual([t['id'] for t in entry['twin_items']],['plex:5'])
        self.assertEqual({i['id'] for i in self.lib.catalog.listing({'issue':'duplicate'})['items']},{'plex:4','plex:5'})

    def test_search_and_filters(self):
        self.assertEqual(list(self.entries(q='盗梦')),['盗梦空间'])
        self.assertEqual(set(self.entries(section='2')),{'某剧','新剧'})
        self.assertEqual(self.lib.catalog.listing({'section':'1'})['total'],4)


class GuessTitleTest(unittest.TestCase):
    def test_names(self):
        self.assertEqual(guess_title('[007：大破天幕杀机]Skyfall.2012.BluRay.2160p.HDR.H265.mkv'),('007：大破天幕杀机',2012))
        self.assertEqual(guess_title('Inception.2010.1080p.BluRay.x264'),('Inception',2010))
        self.assertEqual(guess_title('我推的孩子.2023.4K'),('我推的孩子',2023))
        self.assertEqual(guess_title('The.Office.S01E01.720p.mkv'),('The Office',None))
        self.assertEqual(guess_title('三体'),('三体',None))


if __name__=='__main__':
    unittest.main()
