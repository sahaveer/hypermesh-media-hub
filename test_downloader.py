import itertools
import pathlib
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
import downloader
import main
import config

class DownloaderTests(unittest.TestCase):
    def setUp(self):
        with downloader._lock:
            downloader._download_tasks.clear()

    def test_audio_video_and_both_destinations(self):
        with tempfile.TemporaryDirectory(dir=config.DATA_DIR) as directory:
            root=pathlib.Path(directory)
            for media_format in ('audio','video'):
                for destination in ('client','server'):
                    task_id=media_format+'_'+destination
                    task={'id':task_id,'title':'Fetching metadata...','status':'pending','target_dir':str(root)}
                    downloader._download_tasks[task_id]=task
                    extension='mp3' if media_format=='audio' else 'mp4'
                    output=root/(task_id+'_Demo.'+extension)
                    options=[]
                    class FakeYDL:
                        def __init__(self, opts): options.append(opts)
                        def __enter__(self): return self
                        def __exit__(self,*args): pass
                        def prepare_filename(self, entry): return str(output)
                        def extract_info(self, url, download):
                            output.write_bytes(b'x'*2048)
                            options[-1]['progress_hooks'][0]({'status':'downloading','downloaded_bytes':1024,'total_bytes':2048,'info_dict':{'title':'Demo'}})
                            return {'title':'Demo','duration':1}
                    with patch('downloader.yt_dlp.YoutubeDL',FakeYDL), patch('downloader.scanner.parse_media_file',return_value={'id':'indexed','title':'Demo'}), patch('database.upsert_song') as upsert, patch('downloader.Thread'):
                        downloader._perform_download(task_id,'https://example.com/media',media_format,'720p',root,destination)
                    self.assertEqual(task['status'],'completed')
                    self.assertEqual(downloader.get_download_file(task_id),output.resolve())
                    self.assertEqual(options[0]['socket_timeout'],20)
                    self.assertTrue(options[0]['noplaylist'])
                    self.assertEqual(upsert.call_count,1 if destination=='server' else 0)
                    self.assertIn('/api/downloads/' if destination=='client' else '/api/stream/',task['download_url'])

    def test_no_file_cannot_be_completed_or_return_unrelated_media(self):
        with tempfile.TemporaryDirectory(dir=config.DATA_DIR) as directory:
            root=pathlib.Path(directory)
            (root/'existing.mp4').write_bytes(b'x'*2048)
            downloader._download_tasks['missing']={'id':'missing','title':'Fetching metadata...','status':'pending','target_dir':str(root)}
            fake=MagicMock()
            fake.__enter__.return_value=fake
            fake.extract_info.return_value={'title':'Not downloaded'}
            fake.prepare_filename.return_value=str(root/'missing.mp4')
            with patch('downloader.yt_dlp.YoutubeDL',return_value=fake):
                downloader._perform_download('missing','https://example.com/file','video','best',root)
            self.assertEqual(downloader.get_download_status('missing')['status'],'failed')
            self.assertIsNone(downloader.get_download_file('missing'))

    def test_worker_reports_folder_creation_failure(self):
        with tempfile.TemporaryDirectory(dir=config.DATA_DIR) as directory:
            obstacle=pathlib.Path(directory)/'file'
            obstacle.write_text('not a directory')
            downloader._download_tasks['blocked']={'id':'blocked','url':'https://example.com/media','format':'audio','quality':'best','target_dir':str(obstacle/'child'),'destination':'client','owner_id':'test','owner_name':'Test','title':'Fetching metadata...','status':'pending'}
            downloader._run_download('blocked','https://example.com/media','audio','best',obstacle/'child','client')
            task=downloader.get_download_status('blocked')
            self.assertEqual(task['status'],'failed')
            self.assertTrue(task['error'])

    def test_browser_cookies_configured_in_ydl_opts(self):
        with tempfile.TemporaryDirectory(dir=config.DATA_DIR) as directory:
            root = pathlib.Path(directory)
            task_id = "test_cookies"
            task = {'id': task_id, 'title': 'Fetching metadata...', 'status': 'pending', 'target_dir': str(root)}
            downloader._download_tasks[task_id] = task
            output = root / (task_id + '_Demo.mp4')
            options = []

            class FakeYDL:
                def __init__(self, opts): options.append(opts)
                def __enter__(self): return self
                def __exit__(self, *args): pass
                def prepare_filename(self, entry): return str(output)
                def extract_info(self, url, download):
                    output.write_bytes(b'x'*1024)
                    return {'title': 'Demo Instagram Reel', 'duration': 15}

            with patch('downloader.yt_dlp.YoutubeDL', FakeYDL), patch('downloader.scanner.parse_media_file', return_value={'id': 'ig_reel', 'title': 'Demo Reel'}), patch('database.upsert_song'), patch('downloader.Thread'):
                downloader._perform_download(task_id, 'https://www.instagram.com/reel/C3xyz99/', 'video', 'best', root, 'server', cookies_browser='chrome')

            self.assertEqual(task['status'], 'completed')
            self.assertEqual(options[0].get('cookiesfrombrowser'), ('chrome',))

    def test_start_download_respects_cookies_browser_override(self):
        with patch('downloader.Thread') as mock_thread:
            task_id = downloader.start_download(
                url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                media_format="video",
                quality="1080p",
                destination="server",
                cookies_browser="firefox"
            )
            task = downloader.get_download_status(task_id)
            self.assertEqual(task["cookies_browser"], "firefox")
            mock_thread.assert_called_once()
            args = mock_thread.call_args[1]["args"]
            self.assertEqual(args[-1], "firefox")  # cookies_browser is the last argument

    def test_missing_youtube_support_is_actionable(self):
        with patch('downloader.importlib.util.find_spec',return_value=None):
            with self.assertRaisesRegex(RuntimeError,'yt-dlp'):
                downloader._youtube_runtime()

    def test_stalled_metadata_worker_is_stopped(self):
        downloader._download_tasks['stalled']={'id':'stalled','title':'Fetching metadata...','status':'pending'}
        process=MagicMock()
        process.poll.return_value=None
        process.stdout=[]
        with patch('downloader.subprocess.Popen',return_value=process), patch('downloader.subprocess.run'), patch('downloader.os.killpg',create=True), patch('downloader.time.monotonic',side_effect=itertools.chain([0,121],itertools.repeat(121))), patch('downloader.Thread'):
            downloader._run_download('stalled','https://example.com/file','audio','best',pathlib.Path('.'),'client')
        self.assertEqual(downloader.get_download_status('stalled')['status'],'failed')
        self.assertIn('timed out',downloader.get_download_status('stalled')['error'])
        process.wait.assert_called_once()

    def test_search_youtube_parses_flat_entries(self):
        fake_ydl = MagicMock()
        fake_ydl.__enter__.return_value = fake_ydl
        fake_ydl.extract_info.return_value = {
            'entries': [
                {'id': 'abc123', 'title': 'Ocean Eyes', 'channel': 'Billie Eilish', 'duration': 200},
                {'id': 'def456', 'title': 'Ocean Eyes Remix', 'uploader': 'Remix Channel', 'duration': 180},
            ]
        }
        with patch('downloader.yt_dlp.YoutubeDL', return_value=fake_ydl):
            results = downloader.search_youtube('Ocean Eyes', limit=5)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]['url'], 'https://www.youtube.com/watch?v=abc123')
        self.assertEqual(results[0]['channel'], 'Billie Eilish')
        self.assertEqual(results[1]['channel'], 'Remix Channel')

    def test_downloader_search_prefers_existing_server_song_over_youtube(self):
        client = TestClient(main.app)
        server_row = {
            'id': 'song-local-1',
            'title': 'Blinding Lights',
            'artist': 'The Weeknd',
            'album': 'After Hours',
            'media_type': 'audio',
            'duration': 200,
            'owner_name': 'Home Hub',
        }
        with patch('main.database.get_songs', return_value=[server_row]), patch('main.downloader.search_youtube') as mock_yt:
            res = client.get('/api/downloader/search', params={'q': 'Blinding Lights'})
        self.assertEqual(res.status_code, 200)
        payload = res.json()
        self.assertTrue(payload['in_library'])
        self.assertEqual(len(payload['local_matches']), 1)
        self.assertEqual(payload['local_matches'][0]['id'], 'song-local-1')
        self.assertEqual(payload['youtube_results'], [])
        mock_yt.assert_not_called()

    def test_downloader_search_falls_back_to_youtube_when_not_on_server(self):
        client = TestClient(main.app)
        yt_items = [
            {
                'id': 'yt-1',
                'title': 'New Track Official Video',
                'channel': 'ArtistVEVO',
                'duration': 210.0,
                'thumbnail': 'https://i.ytimg.com/vi/yt-1/hqdefault.jpg',
                'url': 'https://www.youtube.com/watch?v=yt-1',
                'source': 'youtube',
            }
        ]
        with patch('main.database.get_songs', return_value=[]), patch('main.downloader.search_youtube', return_value=yt_items) as mock_yt:
            res = client.get('/api/downloader/search', params={'q': 'New Track'})
        self.assertEqual(res.status_code, 200)
        payload = res.json()
        self.assertFalse(payload['in_library'])
        self.assertEqual(payload['local_matches'], [])
        self.assertEqual(len(payload['youtube_results']), 1)
        self.assertEqual(payload['youtube_results'][0]['id'], 'yt-1')
        mock_yt.assert_called_once()

    def test_pause_resume_and_clear_preserves_paused_task(self):
        client = TestClient(main.app)
        with tempfile.TemporaryDirectory(dir=config.DATA_DIR) as directory:
            root = pathlib.Path(directory)
            part_file = root / 'Demo [vid1].mp4.part'
            part_file.write_bytes(b'partial')
            proc = MagicMock()
            proc.poll.return_value = None
            task_id = 'pause-task-1'
            with downloader._lock:
                downloader._download_tasks[task_id] = {
                    'id': task_id,
                    'url': 'https://www.youtube.com/watch?v=vid1',
                    'title': 'Demo Video',
                    'format': 'video',
                    'quality': 'best',
                    'destination': 'server',
                    'owner_id': 'host',
                    'owner_name': 'Host',
                    'cookies_browser': None,
                    'target_playlist_id': None,
                    'status': 'downloading',
                    'progress': 42.5,
                    'speed': 1024,
                    'eta': 10,
                    'target_dir': str(root),
                    'error': None,
                    '_existing_files': [],
                    '_tracked_files': [str(root / 'Demo [vid1].mp4')],
                    '_video_id': 'vid1',
                }
                downloader._active_processes[task_id] = proc

            with patch('downloader.subprocess.run'), patch('downloader.os.killpg', create=True):
                res = client.post(f'/api/downloads/{task_id}/pause')
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.json()['task']['status'], 'paused')
            self.assertTrue(part_file.exists())
            proc.wait.assert_called_once()

            # Clear inactive should keep paused downloads
            downloader.clear_downloads()
            self.assertIsNotNone(downloader.get_download_status(task_id))

            with patch('downloader.Thread') as mock_thread:
                res_resume = client.post(f'/api/downloads/{task_id}/resume')
            self.assertEqual(res_resume.status_code, 200)
            self.assertEqual(res_resume.json()['task']['status'], 'pending')
            mock_thread.assert_called_once()

    def test_delete_while_processing_kills_worker_and_cleans_only_task_files(self):
        client = TestClient(main.app)
        with tempfile.TemporaryDirectory(dir=config.DATA_DIR) as directory:
            root = pathlib.Path(directory)
            preexisting = root / 'Keep Me.mp3'
            preexisting.write_bytes(b'existing-audio')
            partial = root / 'In Progress [abc999].webm.part'
            partial.write_bytes(b'partial-bytes')
            temp_media = root / 'In Progress [abc999].webm'
            temp_media.write_bytes(b'temp-bytes')

            proc = MagicMock()
            proc.poll.return_value = None
            task_id = 'del-proc-1'
            with downloader._lock:
                downloader._download_tasks[task_id] = {
                    'id': task_id,
                    'url': 'https://www.youtube.com/watch?v=abc999',
                    'title': 'In Progress',
                    'format': 'audio',
                    'quality': 'best',
                    'destination': 'server',
                    'owner_id': 'host',
                    'owner_name': 'Host',
                    'cookies_browser': None,
                    'target_playlist_id': None,
                    'status': 'processing',
                    'progress': 100,
                    'target_dir': str(root),
                    'error': None,
                    '_existing_files': ['Keep Me.mp3'],
                    '_tracked_files': [str(temp_media), str(partial)],
                    '_video_id': 'abc999',
                }
                downloader._active_processes[task_id] = proc

            with patch('downloader.subprocess.run'), patch('downloader.os.killpg', create=True):
                res = client.delete(f'/api/downloads/{task_id}')
            self.assertEqual(res.status_code, 200)
            self.assertIsNone(downloader.get_download_status(task_id))
            proc.wait.assert_called_once()
            self.assertFalse(partial.exists())
            self.assertFalse(temp_media.exists())
            self.assertTrue(preexisting.exists())

    def test_clip_range_subtitles_and_4k_options(self):
        with tempfile.TemporaryDirectory(dir=config.DATA_DIR) as directory:
            root = pathlib.Path(directory)
            task_id = 'clip_sub_4k'
            task = {'id': task_id, 'title': 'Fetching metadata...', 'status': 'pending', 'target_dir': str(root)}
            downloader._download_tasks[task_id] = task
            output = root / (task_id + '_Clip.mp4')
            options = []

            class FakeYDL:
                def __init__(self, opts): options.append(opts)
                def __enter__(self): return self
                def __exit__(self, *args): pass
                def prepare_filename(self, entry): return str(output)
                def extract_info(self, url, download):
                    output.write_bytes(b'x' * 1024)
                    return {'title': 'Clip Video', 'duration': 15}

            with patch('downloader.yt_dlp.YoutubeDL', FakeYDL), \
                 patch('downloader.scanner.parse_media_file', return_value={'id': 'clip1', 'title': 'Clip Video'}), \
                 patch('database.upsert_song'), \
                 patch('downloader.Thread'):
                downloader._perform_download(
                    task_id, 'https://example.com/watch?v=clip1', 'video', '4k', root, 'server',
                    clip_start='0:10', clip_end='0:25', subtitles=True,
                )

            self.assertEqual(task['status'], 'completed')
            self.assertIn('2160', options[0]['format'])
            self.assertTrue(options[0].get('force_keyframes_at_cuts'))
            self.assertIsNotNone(options[0].get('download_ranges'))
            self.assertTrue(options[0].get('writesubtitles'))

    def test_concurrency_queue_and_mid_download_edit(self):
        client = TestClient(main.app)
        with patch('downloader.settings.load_settings', return_value={'max_concurrent_downloads': 1, 'download_browser_cookies': ''}), \
             patch('downloader.Thread'):
            t1 = downloader.start_download('https://example.com/1', media_format='audio', destination='client')
            t2 = downloader.start_download('https://example.com/2', media_format='audio', destination='client')
            self.assertEqual(downloader.get_download_status(t1)['status'], 'pending')
            self.assertEqual(downloader.get_download_status(t2)['status'], 'queued')

            res = client.patch(f'/api/downloads/{t2}', json={'url': 'https://example.com/2-updated', 'proxy': 'http://127.0.0.1:8888'})
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.json()['task']['url'], 'https://example.com/2-updated')
            self.assertEqual(res.json()['task']['proxy'], 'http://127.0.0.1:8888')

    def test_sqlite_download_persistence_marks_interrupted_tasks_paused(self):
        import database
        with tempfile.TemporaryDirectory(dir=config.DATA_DIR) as directory:
            db_file = pathlib.Path(directory) / 'test_dl.db'
            with patch('database.DB_PATH', db_file):
                database.init_db()
                database.upsert_download_task({
                    'id': 'persist-1',
                    'url': 'https://example.com/movie.mp4',
                    'title': 'Movie',
                    'status': 'downloading',
                    'progress': 55.0,
                    'target_dir': directory,
                })
                with downloader._lock:
                    downloader._download_tasks.clear()
                downloader.init_downloads()
                restored = downloader.get_download_status('persist-1')
                self.assertIsNotNone(restored)
                self.assertEqual(restored['status'], 'paused')
                self.assertEqual(restored['progress'], 55.0)

    def test_direct_chunked_http_download_writes_ranges_without_merge(self):
        payload = b'0123456789ABCDEF' * (150 * 1024)  # ~2.4 MB (>2MB triggers 4 chunks)
        with tempfile.TemporaryDirectory(dir=config.DATA_DIR) as directory:
            root = pathlib.Path(directory)
            task_id = 'direct-chunk-1'
            downloader._download_tasks[task_id] = {
                'id': task_id,
                'url': 'https://cdn.example.com/sample.mp4',
                'title': 'Fetching metadata...',
                'format': 'video',
                'quality': 'best',
                'destination': 'client',
                'status': 'pending',
                'progress': 0,
                'target_dir': str(root),
                '_tracked_files': [],
                '_existing_files': [],
            }

            class FakeResp:
                def __init__(self, data, headers):
                    self._data = data
                    self._pos = 0
                    self.headers = headers
                def __enter__(self): return self
                def __exit__(self, *args): pass
                def read(self, n=-1):
                    if self._pos >= len(self._data):
                        return b''
                    chunk = self._data[self._pos:] if n < 0 else self._data[self._pos:self._pos + n]
                    self._pos += len(chunk)
                    return chunk

            class FakeOpener:
                def open(self, req, timeout=15):
                    method = getattr(req, 'method', None) or req.get_method()
                    if method == 'HEAD':
                        return FakeResp(b'', {
                            'Content-Type': 'video/mp4',
                            'Content-Length': str(len(payload)),
                            'Accept-Ranges': 'bytes',
                        })
                    range_hdr = req.headers.get('Range') or req.get_header('Range')
                    if range_hdr and range_hdr.startswith('bytes='):
                        s_str, e_str = range_hdr[6:].split('-')
                        s, e = int(s_str), int(e_str)
                        return FakeResp(payload[s:e + 1], {'Content-Length': str(e - s + 1)})
                    return FakeResp(payload, {'Content-Length': str(len(payload))})

            with patch('downloader.urllib.request.build_opener', return_value=FakeOpener()):
                handled = downloader._perform_chunked_http_download(
                    task_id=task_id,
                    url='https://cdn.example.com/sample.mp4',
                    target_dir=root,
                    media_format='video',
                    destination='client',
                    owner_id='host',
                    owner_name='Host',
                    target_playlist_id=None,
                )

            self.assertTrue(handled)
            task = downloader.get_download_status(task_id)
            self.assertEqual(task['status'], 'completed')
            out_file = pathlib.Path(task['file_path'])
            self.assertTrue(out_file.exists())
            self.assertEqual(out_file.read_bytes(), payload)

if __name__=='__main__': unittest.main()
