"""Real loopback HTTP tests for release preparation and exact export."""
import http.client
import json
import threading
import time
from pathlib import Path
from unittest.mock import patch

from test_release import GitFixture
from web import create_server


class WebTests(GitFixture):
    def setUp(self):
        super().setUp()
        self.server = create_server(self.assistant, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        super().tearDown()

    def request(self, method, path, payload=None, headers=None, raw=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        try:
            connection.request(method, path, (raw if raw is not None else json.dumps(payload or {}).encode())
                               if method == 'POST' else None,
                               {'Content-Type': 'application/json', **(headers or {})})
            response = connection.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            connection.close()

    def prepare(self):
        status, raw, _ = self.request('POST', '/api/releases',
                                      {'base': self.base, 'head': self.head, 'goal': 'Подготовь релиз'})
        self.assertEqual(status, 202, raw)
        job = json.loads(raw)
        for _ in range(200):
            if job['state'] != 'running':
                self.assertEqual(job['state'], 'ready', job)
                return job
            time.sleep(.01)
            job = json.loads(self.request('GET', '/api/releases/' + job['id'])[1])
        self.fail('Job timed out')

    def test_real_page_repository_and_health(self):
        self.assertIn(b'Release Desk', self.request('GET', '/')[1])
        self.assertEqual(json.loads(self.request('GET', '/health')[1])['mode'], 'demo')
        info = json.loads(self.request('GET', '/api/repository')[1])
        self.assertEqual(info['head'], self.head)
        self.assertEqual(info['base'], self.base)

    def test_preview_download_save_and_readback_without_rerunning_ai(self):
        job = self.prepare()
        self.assertTrue(job['events'])
        self.assertFalse(self.assistant.exports.exists())
        path = '/api/releases/' + job['id']
        status, download, headers = self.request('GET', path + '/download')
        self.assertEqual(status, 200)
        self.assertIn('attachment', headers['Content-Disposition'])
        self.assertEqual(download.decode(), job['result']['markdown'])
        with patch.object(self.assistant, 'prepare', side_effect=AssertionError('No regeneration')):
            status, raw, _ = self.request('POST', path + '/save')
        self.assertEqual(status, 200, raw)
        saved = json.loads(raw)
        self.assertEqual(saved['state'], 'saved')
        self.assertEqual(Path(saved['saved']['path']).read_text(), download.decode())
        self.assertEqual(self.request('POST', path + '/save')[0], 200)

    def test_rejects_bad_json_origins_hosts_and_arbitrary_paths(self):
        for raw in (b'{', b'[]', b'{}', b'{"base":"x","head":"y","goal":42}',
                    b'{"base":"x","head":"y","goal":"g","repo":"/etc"}'):
            self.assertEqual(self.request('POST', '/api/releases', raw=raw)[0], 400)
        self.assertEqual(self.request('POST', '/api/releases', raw=b'', headers={'Content-Length': '20000'})[0], 413)
        self.assertEqual(self.request('GET', '/health', headers={'Origin': 'https://other.example'})[0], 403)
        self.assertEqual(self.request('GET', '/health', headers={'Host': 'other.example'})[0], 403)
        self.assertEqual(self.request('GET', '/../../.env')[0], 404)
        self.assertEqual(self.request('POST', '/api/releases/missing/save')[0], 404)

    def test_busy_and_failed_jobs_are_explicit(self):
        self.server.jobs.gate.acquire()
        try:
            self.assertEqual(self.request('POST', '/api/releases', {
                'base': self.base, 'head': self.head, 'goal': 'Релиз'})[0], 503)
        finally:
            self.server.jobs.gate.release()
        _, raw, _ = self.request('POST', '/api/releases', {
            'base': '--help', 'head': self.head, 'goal': 'Релиз'})
        job = json.loads(raw)
        for _ in range(100):
            if job['state'] != 'running':
                break
            time.sleep(.01)
            job = json.loads(self.request('GET', '/api/releases/' + job['id'])[1])
        self.assertEqual(job['state'], 'failed')
        self.assertEqual(job['error']['code'], 'invalid_ref')
        self.assertEqual(self.request('POST', '/api/releases/' + job['id'] + '/save')[0], 409)
