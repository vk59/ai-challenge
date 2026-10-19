#!/usr/bin/env python3
"""Exercise the web API with both goals, including exact preview/save/readback."""
import argparse
import http.client
import json
import shutil
import tempfile
import threading
import time
from pathlib import Path

from assistant import FileAssistant, HERE
from smoke import GOALS
from web import create_server
from web_tasks import WebTasks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    results = []
    with tempfile.TemporaryDirectory(prefix='day34-web-smoke-') as temporary:
        root = Path(temporary) / 'project'
        shutil.copytree(HERE / 'sample', root)
        server = create_server(WebTasks(FileAssistant(root, 'cloud' if args.live else 'demo')), port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def request(path, data=None):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=60)
            try:
                connection.request('GET' if data is None else 'POST', path,
                                   None if data is None else json.dumps(data).encode(),
                                   {'Content-Type': 'application/json'})
                response = connection.getresponse()
                body = json.loads(response.read())
                if response.status >= 400:
                    raise RuntimeError(body)
                return body
            finally:
                connection.close()

        try:
            assert request('/health')['status'] == 'ok'
            for goal in GOALS:
                before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
                task = request('/api/tasks', {'goal': goal})
                deadline = time.monotonic() + 240
                while task['state'] == 'running' and time.monotonic() < deadline:
                    time.sleep(.5)
                    task = request('/api/tasks/' + task['id'])
                assert task['state'] == 'ready', task
                preview = task['result']
                assert len(preview['files_read']) >= 3 and preview['diff'] and task['events']
                assert before == {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
                saved = request('/api/tasks/' + task['id'] + '/apply', {})
                assert saved['state'] == 'applied' and saved['result']['diff'] == preview['diff']
                assert saved['result']['applied_files']
                for path in saved['result']['applied_files']:
                    assert (root / path).is_file()
                results.append(saved)
                print(f"PASS web [{preview['mode']}] {goal}\nSaved: {saved['result']['applied_files']}", flush=True)
            report = '\n'.join((root / p).read_text() for p in results[0]['result']['applied_files'])
            assert all(path in report for path in ('src/auth.py', 'src/web.py', 'src/cli.py'))
            docs = (root / 'docs/auth.md').read_text()
            assert all(text in docs for text in ('mfa_code', '3600', 'Этот раздел нужно сохранить.'))
            assert 'Устаревшее описание' not in docs
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
            if args.output:
                args.output.mkdir(parents=True, exist_ok=True)
                (args.output / 'web-report.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))
                shutil.copytree(root, args.output / 'project', dirs_exist_ok=True)
    print('2/2 web scenarios passed')


if __name__ == '__main__':
    main()
