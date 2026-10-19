#!/usr/bin/env python3
"""Exercise the actual repository over HTTP; --live invokes direct DeepSeek."""
import argparse
import http.client
import json
import tempfile
import threading
import time
from pathlib import Path

from release import REPO, ReleaseAssistant, validate_plan
from web import create_server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--repo', type=Path, default=REPO)
    parser.add_argument('--base')
    parser.add_argument('--head')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='day35-smoke-') as temporary:
        output = args.output or Path(temporary)
        assistant = ReleaseAssistant(args.repo, 'cloud' if args.live else 'demo', exports=output / 'exports')
        server = create_server(assistant, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def request(path, body=None):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=30)
            try:
                connection.request('GET' if body is None else 'POST', path,
                                   None if body is None else json.dumps(body).encode(),
                                   {'Content-Type': 'application/json'})
                response = connection.getresponse()
                raw = response.read()
                assert response.status < 400, raw
                return json.loads(raw)
            finally:
                connection.close()

        try:
            info = request('/api/repository')
            job = request('/api/releases', {'base': args.base or info['base'], 'head': args.head or info['head'],
                                           'goal': 'Подготовь релиз: важные изменения для пользователя, подтверждённые риски и проверки перед выпуском.'})
            deadline = time.monotonic() + 240
            while job['state'] == 'running' and time.monotonic() < deadline:
                time.sleep(.5)
                job = request('/api/releases/' + job['id'])
            assert job['state'] == 'ready', job
            result = job['result']
            assert result['evidence']['commits'] and result['evidence']['files']
            assert result['evidence']['diff']
            validate_plan(result['plan'], result['evidence'])
            assert result['plan']['highlights'] and result['plan']['checklist']
            preview = result['markdown']
            saved = request('/api/releases/' + job['id'] + '/save', {})
            assert saved['state'] == 'saved'
            assert Path(saved['saved']['path']).read_text() == preview
            output.mkdir(parents=True, exist_ok=True)
            (output / 'report.json').write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding='utf-8')
            print(f"PASS [{assistant.mode}] actual Git → release notes → exact export/readback", flush=True)
            print(f"Commits: {len(result['evidence']['commits'])}; files: {len(result['evidence']['files'])}; coverage: {result['evidence']['coverage']}")
            print(preview)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == '__main__':
    main()
