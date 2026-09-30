import csv
import hashlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.request import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'CoralNet_Image_Downloader_GUI_v3'))
import coralnet_downloader_core as core
from coralnet_session import CoralNetSession, AccessError, SignedURLExpired, sanitize_url, safe_error, _SafeRedirect

BASE = 'https://coralnet.ucsd.edu'
DETAIL = BASE + '/image/123/view/'
SIGNED = 'https://bucket.aws/media/images/file.jpg?X-Amz-Credential=SECRET&X-Amz-Signature=SECRET'
IMAGE = b'\xff\xd8\xff' + b'original image bytes' * 50


class Response(io.BytesIO):
    def __init__(self, body=IMAGE, url=SIGNED, headers=None):
        super().__init__(body)
        self.url = url
        self.headers = headers or {'Content-Type': 'image/jpeg', 'Content-Length': str(len(body))}
    def geturl(self):
        return self.url


def browse(page, image_id, status='confirmed', count=2):
    selected = '<select name="annotation_status"><option value="confirmed" selected>Confirmed</option></select>'
    return (f'<title>Reef - Browse Images | CoralNet</title>{selected}'
            f'<p>Page {page} of 2 Showing 1-1 of {count}</p>'
            f'<a href="/image/{image_id}/view/"><img src="thumb.jpg" class="thumb {status}"></a>')


def source(ids=('123',), confirmed=False):
    return core.SourceInfo('8794', core.build_browse_url('8794'), 'Reef', 1, len(ids),
                           tuple((i, BASE + f'/image/{i}/view/') for i in ids),
                           tuple((i, 'confirmed') for i in ids), confirmed)


class DownloaderTests(unittest.TestCase):
    def test_source_parsing(self):
        for value in ['8794', BASE + '/source/8794/', BASE + '/source/8794/browse/images/?page=9&evil=1']:
            self.assertEqual(core.normalize_source(value), ('8794', BASE + '/source/8794/browse/images/'))
        for value in ['https://evil.com/source/8794/', 'https://coralnet.ucsd.edu@evil.com/source/8794/', '']:
            with self.assertRaises(ValueError):
                core.normalize_source(value)

    def test_browse_urls(self):
        self.assertEqual(core.build_browse_url('8794'), BASE + '/source/8794/browse/images/')
        confirmed = core.build_browse_url('8794', 'confirmed')
        self.assertEqual(confirmed, BASE + '/source/8794/browse/images/?annotation_status=confirmed')
        self.assertEqual(core._page_url(confirmed + '&evil=1', 2), confirmed + '&page=2')

    def test_filtered_pagination(self):
        with patch.object(core, '_fetch_html', side_effect=[(browse(1, '123'), BASE), (browse(2, '124'), BASE)]) as fetch:
            result = core.discover_source('8794', Event(), lambda x: None, lambda x: None,
                                          lambda *x: None, 5, 1, 0, True, CoralNetSession())
            self.assertEqual(len(result.details), 2)
            self.assertEqual(fetch.call_args_list[1].args[0], core.build_browse_url('8794', 'confirmed', 2))
            self.assertEqual(dict(result.annotation_states), {'123': 'confirmed', '124': 'confirmed'})

    def test_filter_not_acknowledged_fails(self):
        with patch.object(core, '_fetch_html', return_value=('<title>Changed</title>', BASE)):
            with self.assertRaisesRegex(RuntimeError, 'acknowledge'):
                core.discover_source('8794', Event(), lambda x: None, lambda x: None, lambda *x: None, 5, 1, 0, True)

    def test_denial_and_expiry_on_any_page(self):
        for body in ["<p>Sorry, you don&#x27;t have permission to view this page.</p>", '<input type="password">']:
            session = CoralNetSession()
            with self.assertRaises(AccessError):
                session.check_html(body, BASE + '/source/8794/browse/images/')
        session = CoralNetSession()
        session._session_cookie = 'secret'
        session.state = 'Connected'
        with self.assertRaises(AccessError) as error:
            session.check_html('<a href="/accounts/login/">Sign in</a>', BASE)
        self.assertEqual(error.exception.state, 'Session expired')
        self.assertFalse(session._session_cookie)

    def test_import_requires_verified_login(self):
        session = CoralNetSession()
        with patch.object(session, 'open', return_value=Response(b'<a href="/accounts/logout/">Sign out</a>', BASE)):
            session.connect('local-secret', Event())
        self.assertTrue(session.is_authenticated())
        session.disconnect()
        self.assertFalse(session.is_authenticated())
        with patch.object(session, 'open', return_value=Response(b'<a href="/accounts/login/">Sign in</a>', BASE)):
            with self.assertRaises(AccessError):
                session.connect('expired-secret', Event())
        self.assertFalse(session._session_cookie)

    def test_session_host_boundary(self):
        session = CoralNetSession()
        session._session_cookie = 'SECRET'
        with patch.object(session._opener, 'open', return_value=Response()) as coral, patch.object(session._storage_opener, 'open', return_value=Response()) as storage:
            session.open(Request(BASE + '/source/8794/'), 5).close()
            self.assertEqual(coral.call_args.args[0].get_header('Cookie'), 'sessionid=SECRET')
            request = Request(BASE + '/old/')
            request.add_unredirected_header('Cookie', 'sessionid=SECRET')
            redirected = _SafeRedirect().redirect_request(request, None, 302, '', {}, BASE + '/new/')
            self.assertEqual(redirected.get_header('Cookie'), 'sessionid=SECRET')
            session.open(Request(SIGNED), 5).close()
            self.assertIsNone(storage.call_args.args[0].get_header('Cookie'))
            session.open(Request('https://sub.coralnet.ucsd.edu/image.jpg'), 5).close()
            self.assertIsNone(storage.call_args.args[0].get_header('Cookie'))
        with self.assertRaises(RuntimeError):
            session.open(Request('http://' + 'coralnet.ucsd.edu/'), 5)
        with self.assertRaises(RuntimeError):
            _SafeRedirect().redirect_request(Request(BASE), None, 302, '', {}, 'https://evil.com/')

    def test_401_403_stop_immediately(self):
        for code in [401, 403]:
            session = CoralNetSession()
            session._session_cookie = 'SECRET'
            with patch.object(session._opener, 'open', side_effect=HTTPError(BASE, code, 'SECRET', {}, None)) as opener:
                with self.assertRaises(AccessError):
                    core._fetch_html(BASE, Event(), 5, 4, session)
                self.assertEqual(opener.call_count, 1)

    def test_sanitization(self):
        self.assertNotIn('SECRET', sanitize_url(SIGNED))
        self.assertIn('[REDACTED]', sanitize_url(SIGNED))
        self.assertNotIn('SECRET', safe_error(URLError(SIGNED)))
        self.assertNotIn('SECRET', safe_error(RuntimeError('sessionid=SECRET')))
        self.assertNotIn('SECRET', safe_error(HTTPError(SIGNED, 403, 'SECRET', {}, None)))

    def test_duplicate_names(self):
        used = set()
        first = core._unique_name('reef.jpg', '123', used)
        second = core._unique_name('reef.jpg', '124', used)
        self.assertEqual(first, 'reef.jpg')
        self.assertNotEqual(first.casefold(), second.casefold())

    def test_original_extraction_rejects_thumbnails(self):
        original = f'<title>reef.JPG | CoralNet</title><div id="original_image_container"><img src="{SIGNED}"></div><img src="https://bucket.aws/media/images/file.jpg.800x0_q85.jpg"><p>Annotation status: <b>Confirmed (completed)</b></p>'
        with patch.object(core, '_fetch_html', return_value=(original, DETAIL)):
            record = core._extract_record('123', DETAIL, Event(), 5, 1)
            self.assertEqual(record.image_url, SIGNED)
            self.assertEqual(record.annotation_status, 'confirmed')
            self.assertEqual(record.original_filename, 'reef.JPG')
        with patch.object(core, '_fetch_html', return_value=('<title>reef.jpg | CoralNet</title><img src="https://bucket.aws/media/images/file.jpg.800x0_q85.jpg">', DETAIL)):
            with self.assertRaisesRegex(RuntimeError, 'original-resolution'):
                core._extract_record('123', DETAIL, Event(), 5, 1)

    def test_expired_signed_url_refresh_and_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'reef.jpg'
            session = Mock()
            session.open.side_effect = [SignedURLExpired(), Response()]
            refresh = Mock(return_value=SIGNED + '&fresh=1')
            size, digest = core._download_file(SIGNED, target, Event(), 5, 2, session, refresh)
            refresh.assert_called_once()
            self.assertEqual(target.read_bytes(), IMAGE)
            self.assertEqual(digest, hashlib.sha256(IMAGE).hexdigest())
            self.assertEqual(size, len(IMAGE))
            self.assertFalse(target.with_suffix('.jpg.part').exists())

    def test_truncated_download_not_completed(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Mock()
            session.open.return_value = Response(IMAGE, headers={'Content-Length': str(len(IMAGE) + 20)})
            target = Path(directory) / 'reef.jpg'
            with self.assertRaises(RuntimeError):
                core._download_file(SIGNED, target, Event(), 5, 1, session)
            self.assertFalse(target.exists())
            self.assertFalse(target.with_suffix('.jpg.part').exists())

    def test_cancel_removes_partial_file(self):
        with tempfile.TemporaryDirectory() as directory:
            stop = Event()
            response = Response()
            original_read = response.read
            def read(*args):
                stop.set()
                return original_read(*args)
            response.read = read
            session = Mock()
            session.open.return_value = response
            target = Path(directory) / 'reef.jpg'
            with self.assertRaises(core.DownloadCancelled):
                core._download_file(SIGNED, target, stop, 5, 1, session)
            self.assertFalse(target.exists())
            self.assertFalse(target.with_suffix('.jpg.part').exists())

    def run_job(self, output, **kwargs):
        return core.run_download('8794', output, 0, Event(), lambda x: None,
                                 lambda x: None, lambda *x: None, lambda x: None, delay=0, **kwargs)

    def test_resume_checks_checksum_and_skips_download(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            image = output / 'reef.jpg'
            image.write_bytes(IMAGE)
            row = {'source_id': '8794', 'image_id': '123', 'local_filename': 'reef.jpg',
                   'bytes': str(len(IMAGE)), 'sha256': hashlib.sha256(IMAGE).hexdigest(),
                   'download_status': 'downloaded'}
            core._write_manifest(output / 'download_manifest.csv', {'123': row})
            with patch.object(core, 'discover_source', return_value=source()), patch.object(core, '_extract_record') as extract, patch.object(core, '_download_file') as download:
                summary = self.run_job(output)
                self.assertEqual(summary.previously_complete, 1)
                extract.assert_not_called()
                download.assert_not_called()
            image.write_bytes(b'x' * len(IMAGE))
            record = core.ImageRecord('123', DETAIL, 'reef.jpg', SIGNED, '')
            with patch.object(core, 'discover_source', return_value=source()), patch.object(core, '_extract_record', return_value=record), patch.object(core, '_download_file', return_value=(len(IMAGE), hashlib.sha256(IMAGE).hexdigest())) as download:
                summary = self.run_job(output)
                self.assertEqual(summary.previously_complete, 0)
                download.assert_called_once()

    def test_manifest_and_metadata_never_persist_signed_urls(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            record = core.ImageRecord('123', DETAIL, 'reef.jpg', SIGNED, SIGNED)
            with patch.object(core, 'discover_source', return_value=source(confirmed=True)), patch.object(core, '_extract_record', return_value=record), patch.object(core, '_download_file', return_value=(len(IMAGE), hashlib.sha256(IMAGE).hexdigest())):
                self.run_job(output, confirmed_only=True)
            content = (output / 'download_manifest.csv').read_text() + (output / 'SOURCE_INFO.txt').read_text()
            self.assertNotIn('SECRET', content)
            self.assertNotIn('bucket.aws', content)
            self.assertIn('annotation_status=confirmed', content)
            self.assertIn('Images completed: 1', content)

    def test_access_denied_during_download_aborts_run(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(core, 'discover_source', return_value=source(('123', '124'))), patch.object(core, '_extract_record', side_effect=AccessError('No access', 'Access denied.')) as extract:
                with self.assertRaises(AccessError):
                    self.run_job(Path(directory))
                self.assertEqual(extract.call_count, 1)
                self.assertIn('stopped', (Path(directory) / 'SOURCE_INFO.txt').read_text())

    def test_retries_preserve_atomic_original_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Mock()
            session.open.side_effect = [URLError(SIGNED), Response()]
            target = Path(directory) / 'reef.jpg'
            with patch.object(core.time, 'sleep'):
                core._download_file(SIGNED, target, Event(), 5, 2, session)
            self.assertEqual(session.open.call_count, 2)
            self.assertEqual(target.read_bytes(), IMAGE)

    def test_test_limit_collision_and_untracked_file_preservation(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            untracked = output / 'reef.jpg'
            untracked.write_bytes(b'keep existing file')
            ids = tuple(str(i) for i in range(1, 13))
            session = Mock()
            session.is_authenticated.return_value = False
            session.open.side_effect = lambda *args, **kwargs: Response()
            def extract(image_id, detail_url, **kwargs):
                return core.ImageRecord(image_id, detail_url, 'reef.jpg', SIGNED, '', 'confirmed')
            with patch.object(core, 'discover_source', return_value=source(ids, True)), patch.object(core, '_extract_record', side_effect=extract):
                summary = core.run_download('8794', output, 10, Event(), lambda x: None,
                                            lambda x: None, lambda *x: None, lambda x: None,
                                            delay=0, confirmed_only=True, session=session)
            self.assertEqual(summary.downloaded, 10)
            self.assertEqual(untracked.read_bytes(), b'keep existing file')
            rows = list(csv.DictReader(io.StringIO((output / 'download_manifest.csv').read_text())))
            self.assertEqual(len({r['local_filename'].casefold() for r in rows}), 10)
            self.assertTrue(all(r['annotation_status'] == 'confirmed' for r in rows))
            self.assertIn('Images discovered: 12', (output / 'SOURCE_INFO.txt').read_text())
            self.assertIn('Images selected: 10', (output / 'SOURCE_INFO.txt').read_text())

    def test_empty_confirmed_source(self):
        document = '<select name="annotation_status"><option value="confirmed" selected>Confirmed</option></select><p>No images found.</p>'
        with patch.object(core, '_fetch_html', return_value=(document, BASE)):
            result = core.discover_source('8794', Event(), lambda x: None, lambda x: None,
                                          lambda *x: None, 5, 1, 0, True)
        self.assertFalse(result.details)

    def test_legacy_manifest_migrates_without_signed_urls(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'download_manifest.csv'
            with path.open('w', newline='') as handle:
                writer = csv.DictWriter(handle, fieldnames=['image_id', 'saved_filename', 'status', 'original_url'])
                writer.writeheader()
                writer.writerow({'image_id': '123', 'saved_filename': 'reef.jpg', 'status': 'downloaded', 'original_url': SIGNED})
            rows = core._load_manifest(path)
            self.assertEqual(rows['123']['local_filename'], 'reef.jpg')
            core._write_manifest(path, rows)
            self.assertNotIn('SECRET', path.read_text())


if __name__ == '__main__':
    unittest.main()
