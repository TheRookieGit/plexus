import unittest

from tests.test_catalog import FakePlex, Fixture


class DeleterTest(Fixture,unittest.TestCase):
    def setUp(self):
        super().setUp()
        FakePlex.calls = []
        self.lib.start_sync = lambda:True
        self.lib.deleter.background = False

    def delete(self,**data):
        preview = self.lib.deleter.preview(data)
        return preview,self.lib.deleter.confirm(preview['token'])

    def test_movie_beside_other_movies_takes_only_its_own_files(self):
        preview,result = self.delete(id='plex:1')
        self.assertEqual(sorted(t['kind'] for t in preview['targets']),['file','file','file'])
        self.assertFalse((self.movies/'盗梦空间 (2010).mkv').exists())
        self.assertFalse((self.movies/'盗梦空间 (2010).chs.ass').exists())
        self.assertFalse((self.movies/'盗梦空间 (2010)-poster.jpg').exists())
        # A different title that merely starts with the same words stays.
        self.assertTrue((self.movies/'盗梦空间续集.nfo').exists())
        self.assertTrue((self.movies/'[无名之辈]Nobody.2018.1080p.mp4').exists())
        self.assertEqual(result['deleted'],3)

    def test_movie_folder_goes_as_a_whole(self):
        preview,_ = self.delete(id='plex:4')
        self.assertEqual([(t['kind'],t['videos']) for t in preview['targets']],[('folder',2)])
        self.assertFalse((self.movies/'星际穿越 (2014)').exists())
        self.assertTrue(self.movies.exists())

    def test_one_version_of_a_movie(self):
        path = self.movies/'星际穿越 (2014)'/'星际穿越.2160p.mkv'
        preview,_ = self.delete(id='plex:4',scope='file',path=str(path))
        self.assertEqual([t['path'] for t in preview['targets']],[str(path)])
        self.assertTrue((self.movies/'星际穿越 (2014)'/'星际穿越.1080p.mkv').exists())

    def test_episode_and_whole_show(self):
        preview,_ = self.delete(id='plex:10',scope='episode',episode_id='11')
        self.assertEqual(sorted(t['path'].rsplit('\\',1)[1] for t in preview['targets']),['S01E01.ass','S01E01.mkv'])
        self.assertTrue((self.shows/'某剧'/'S01E02.mkv').exists())
        self.lib.catalog.sync(self.lib.plex())
        preview,_ = self.delete(id='plex:10')
        self.assertEqual([t['kind'] for t in preview['targets']],['folder'])
        self.assertFalse((self.shows/'某剧').exists())
        self.assertIn(('empty_trash','2'),FakePlex.calls)

    def test_season_leaves_files_plex_does_not_list(self):
        # Season 1 in Plex is two of the three videos in the folder, so the folder itself stays.
        preview,_ = self.delete(id='plex:10',scope='season',season=1)
        self.assertTrue(all(t['kind']=='file' for t in preview['targets']))
        self.assertTrue((self.shows/'某剧'/'S01E03.mkv').exists())

    def test_confirmation_is_used_once(self):
        preview = self.lib.deleter.preview({'id':'plex:1'})
        self.lib.deleter.confirm(preview['token'])
        with self.assertRaises(ValueError):
            self.lib.deleter.confirm(preview['token'])

    def test_changed_files_need_a_new_confirmation(self):
        preview = self.lib.deleter.preview({'id':'plex:1'})
        (self.movies/'盗梦空间 (2010).zh.srt').write_text('1')
        with self.assertRaises(ValueError):
            self.lib.deleter.confirm(preview['token'])
        self.assertTrue((self.movies/'盗梦空间 (2010).mkv').exists())

    def test_deleted_files_are_not_reported_missing(self):
        self.delete(id='plex:1')
        self.lib.catalog.sync(self.lib.plex())
        self.assertNotIn('盗梦空间',{i['title'] for i in self.lib.catalog.listing({})['items']})


if __name__=='__main__':
    unittest.main()
