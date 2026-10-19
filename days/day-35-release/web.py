#!/usr/bin/env python3
"""Local release preparation console: Git evidence → DeepSeek → Markdown."""
import argparse
import copy
import json
import threading
import uuid
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from release import HERE, REPO, ReleaseAssistant, ReleaseError


class Jobs:
    def __init__(self, assistant):
        self.assistant = assistant
        self.gate = threading.Lock()
        self.lock = threading.RLock()
        self.jobs = OrderedDict()

    def get(self, job_id):
        with self.lock:
            if job_id not in self.jobs:
                raise ReleaseError('not_found', 'Пакет не найден или устарел. Подготовьте его заново.', 404)
            return copy.deepcopy(self.jobs[job_id])

    def start(self, payload):
        if (not isinstance(payload, dict) or set(payload) != {'base', 'head', 'goal'}
                or not all(isinstance(value, str) and 1 <= len(value.strip()) <= 2000 for value in payload.values())):
            raise ReleaseError('invalid_request', 'Укажите base, head и goal строками.')
        if not self.gate.acquire(blocking=False):
            raise ReleaseError('busy', 'Дождитесь завершения текущей задачи.', 503)
        job_id = uuid.uuid4().hex
        with self.lock:
            while len(self.jobs) >= 8:
                self.jobs.popitem(last=False)
            self.jobs[job_id] = {'id': job_id, 'state': 'running', 'events': [], 'result': None}
        try:
            threading.Thread(target=self._run, args=(job_id, payload), daemon=True).start()
        except Exception:
            self.gate.release()
            raise
        return self.get(job_id)

    def _run(self, job_id, payload):
        def progress(message):
            with self.lock:
                self.jobs[job_id]['events'].append(message)
        try:
            result = self.assistant.prepare(**payload, on_event=progress)
            with self.lock:
                self.jobs[job_id].update(state='ready', result=result)
        except Exception as exc:
            with self.lock:
                self.jobs[job_id].update(state='failed', error={
                    'code': exc.code if isinstance(exc, ReleaseError) else 'internal_error',
                    'message': str(exc) if isinstance(exc, ReleaseError) else 'Не удалось подготовить пакет.'})
        finally:
            self.gate.release()

    def save(self, job_id):
        job = self.get(job_id)
        if job['state'] == 'saved':
            return job
        if job['state'] != 'ready':
            raise ReleaseError('not_ready', 'Сначала подготовьте пакет.', 409)
        if not self.gate.acquire(blocking=False):
            raise ReleaseError('busy', 'Ассистент занят.', 503)
        try:
            saved = self.assistant.save(job['result'])
            with self.lock:
                self.jobs[job_id].update(state='saved', saved=saved)
            return self.get(job_id)
        finally:
            self.gate.release()


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def log_message(self, *_):
        pass

    def body(self, status, data, content_type='application/json; charset=utf-8', filename=None):
        self.send_response(status)
        headers = {'Content-Type': content_type, 'Content-Length': str(len(data)), 'Cache-Control': 'no-store',
                   'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY', 'Connection': 'close'}
        if filename:
            headers['Content-Disposition'] = 'attachment; filename="' + filename + '"'
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)
        self.close_connection = True

    def json(self, status, data):
        self.body(status, json.dumps(data, ensure_ascii=False).encode())

    def dispatch(self, post=False):
        try:
            hosts = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            if (self.headers.get('Host') not in hosts
                    or self.headers.get('Origin') not in {None, *('http://' + host for host in hosts)}):
                raise ReleaseError('forbidden', 'Запрос с внешнего сайта запрещён.', 403)
            path = urlsplit(self.path).path
            jobs = self.server.jobs
            parts = path.strip('/').split('/')
            if post:
                if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
                    raise ReleaseError('unsupported_media_type', 'Ожидается application/json.', 415)
                try:
                    size = int(self.headers.get('Content-Length', '0'))
                except ValueError:
                    raise ReleaseError('invalid_request', 'Неверная длина запроса.') from None
                if not 1 <= size <= 16384:
                    raise ReleaseError('body_too_large', 'Допустимо до 16384 байт JSON.', 413)
                try:
                    payload = json.loads(self.rfile.read(size))
                except (ValueError, OSError):
                    raise ReleaseError('invalid_request', 'Некорректный JSON.') from None
                if path == '/api/releases':
                    return self.json(202, jobs.start(payload))
                if len(parts) == 4 and parts[:2] == ['api', 'releases'] and parts[3] == 'save' and payload == {}:
                    return self.json(200, jobs.save(parts[2]))
                raise ReleaseError('invalid_request', 'Неизвестный запрос или лишние поля.')
            if path == '/':
                return self.body(200, (HERE / 'index.html').read_bytes(), 'text/html; charset=utf-8')
            if path == '/health':
                return self.json(200, {'status': 'ok', 'mode': jobs.assistant.mode, 'provider': 'DeepSeek'})
            if path == '/api/repository':
                return self.json(200, jobs.assistant.info())
            if len(parts) in {3, 4} and parts[:2] == ['api', 'releases']:
                job = jobs.get(parts[2])
                if len(parts) == 3:
                    return self.json(200, job)
                if parts[3] == 'download' and job['result']:
                    result = job['result']
                    return self.body(200, result['markdown'].encode(), 'text/markdown; charset=utf-8',
                                     'release-' + result['id'] + '.md')
            raise ReleaseError('not_found', 'Страница не найдена.', 404)
        except ReleaseError as exc:
            self.json(exc.status, {'error': {'code': exc.code, 'message': str(exc)}})
        except Exception:
            self.json(500, {'error': {'code': 'internal_error', 'message': 'Ошибка сервиса.'}})

    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch(post=True)


def create_server(assistant, port=8035):
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    server.jobs = Jobs(assistant)
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', default=str(REPO))
    parser.add_argument('--port', type=int, default=8035)
    parser.add_argument('--demo', action='store_true')
    args = parser.parse_args()
    assistant = ReleaseAssistant(args.repo, mode='demo' if args.demo else 'cloud')
    assistant.info()  # Fail early on invalid Git checkout.
    server = create_server(assistant, args.port)
    print(f'Release Desk: http://127.0.0.1:{server.server_port} ({assistant.mode}, DeepSeek)', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
