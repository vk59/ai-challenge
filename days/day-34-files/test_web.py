"""Actual loopback web API: preview → exact apply, conflict and origin guards."""
import http.client
import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from assistant import FileAssistant
from web import create_server
from web_tasks import WebTasks


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'project'
        shutil.copytree(HERE / 'sample', self.root)
        self.tasks = WebTasks(FileAssistant(self.root, mode='demo'))
        self.server = create_server(self.tasks, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, method, path, data=None, headers=None, raw=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        try:
            body = raw if raw is not None else json.dumps(data or {}).encode()
            connection.request(method, path, body if method == 'POST' else None,
                               {'Content-Type': 'application/json', **(headers or {})})
            response = connection.getresponse()
            content = response.read()
            return response.status, content
        finally:
            connection.close()

    def preview(self, goal='Найди использования login и создай отчёт'):
        status, body = self.request('POST', '/api/tasks', {'goal': goal})
        self.assertEqual(status, 202)
        task_id = json.loads(body)['id']
        for _ in range(200):
            status, body = self.request('GET', '/api/tasks/' + task_id)
            task = json.loads(body)
            if task['state'] != 'running':
                self.assertEqual(task['state'], 'ready', task)
                return task_id, task
            time.sleep(.01)
        self.fail('Preview timed out')

    def test_page_project_health_and_real_file_read(self):
        self.assertIn(b'diff', self.request('GET', '/')[1])
        self.assertEqual(json.loads(self.request('GET', '/health')[1])['mode'], 'demo')
        files = json.loads(self.request('GET', '/api/project')[1])['files']
        self.assertIn('src/auth.py', files)
        status, body = self.request('GET', '/api/file?path=src/auth.py')
        self.assertEqual(status, 200)
        self.assertIn('3600', json.loads(body)['content'])

    def test_preview_apply_is_exact_and_does_not_rerun_agent(self):
        task_id, task = self.preview()
        self.assertTrue(task['result']['diff'])
        self.assertTrue(task['events'])
        self.assertNotIn('_plan', json.dumps(task))
        self.assertFalse((self.root / 'reports').exists())
        with patch.object(self.tasks.assistant, 'run', side_effect=AssertionError('Do not rerun model')):
            status, body = self.request('POST', f'/api/tasks/{task_id}/apply')
        self.assertEqual(status, 200, body)
        saved = json.loads(body)
        self.assertEqual(saved['state'], 'applied')
        self.assertEqual(saved['result']['diff'], task['result']['diff'])
        path = saved['result']['applied_files'][0]
        self.assertIn('src/cli.py', (self.root / path).read_text())
        self.assertIn(path, json.loads(self.request('GET', '/api/project')[1])['files'])
        self.assertEqual(self.request('POST', f'/api/tasks/{task_id}/apply')[0], 200)

    def test_conflict_keeps_external_changes(self):
        task_id, _ = self.preview('Обнови документацию по входу')
        (self.root / 'docs/auth.md').write_text('external change')
        status, body = self.request('POST', f'/api/tasks/{task_id}/apply')
        self.assertEqual(status, 409, body)
        self.assertEqual(json.loads(body)['error']['code'], 'conflict')
        self.assertEqual((self.root / 'docs/auth.md').read_text(), 'external change')

    def test_rejects_bad_requests_and_outside_access(self):
        for raw in (b'{', b'[]', b'{}', b'{"goal":42}', b'{"goal":"x","project":"/etc"}'):
            with self.subTest(raw=raw):
                self.assertEqual(self.request('POST', '/api/tasks', raw=raw)[0], 400)
        self.assertEqual(self.request('POST', '/api/tasks', raw=b'', headers={'Content-Length': '20000'})[0], 413)
        self.assertEqual(self.request('POST', '/api/tasks', {'goal': 'x'}, {'Origin': 'https://other.example'})[0], 403)
        self.assertEqual(self.request('GET', '/health', headers={'Host': 'other.example'})[0], 403)
        self.assertEqual(self.request('GET', '/api/file?path=../outside.md')[0], 403)
        self.assertEqual(self.request('GET', '/api/file?path=.env')[0], 403)
        self.assertEqual(self.request('POST', '/api/tasks/missing/apply')[0], 404)

    def test_busy_task_is_bounded(self):
        self.tasks.gate.acquire()
        try:
            self.assertEqual(self.request('POST', '/api/tasks', {'goal': 'Обнови документацию'})[0], 503)
        finally:
            self.tasks.gate.release()


if __name__ == '__main__':
    unittest.main()
