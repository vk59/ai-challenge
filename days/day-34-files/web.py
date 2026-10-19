#!/usr/bin/env python3
"""Loopback web console for the goal-oriented file assistant."""
import argparse
import json
import shutil
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from assistant import FileAssistant, HERE
from web_tasks import WebError, WebTasks


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def log_message(self, *_):
        pass

    def body(self, status, data, content_type='application/json; charset=utf-8'):
        self.send_response(status)
        for key, value in {'Content-Type': content_type, 'Content-Length': str(len(data)),
                           'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                           'X-Frame-Options': 'DENY', 'Connection': 'close'}.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)
        self.close_connection = True

    def json(self, status, value):
        self.body(status, json.dumps(value, ensure_ascii=False).encode())

    def dispatch(self, post=False):
        try:
            hosts = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            if self.headers.get('Host') not in hosts or self.headers.get('Origin') not in (
                    None, *['http://' + host for host in hosts]):
                raise WebError('forbidden', 'Запрос с внешнего сайта запрещён.', 403)
            url = urlsplit(self.path)
            tasks = self.server.tasks
            if post:
                if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
                    raise WebError('unsupported_media_type', 'Ожидается application/json.', 415)
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                except ValueError:
                    raise WebError('invalid_request', 'Некорректная длина JSON.') from None
                if not 1 <= length <= 16384:
                    raise WebError('body_too_large', 'Допустимо до 16384 байт JSON.', 413)
                try:
                    data = json.loads(self.rfile.read(length))
                except (ValueError, OSError):
                    raise WebError('invalid_request', 'Некорректный JSON.') from None
                if not isinstance(data, dict):
                    raise WebError('invalid_request', 'Ожидается JSON-объект.')
                if url.path == '/api/tasks' and set(data) == {'goal'}:
                    return self.json(202, tasks.start(data['goal']))
                parts = url.path.strip('/').split('/')
                if len(parts) == 4 and parts[:2] == ['api', 'tasks'] and parts[3] == 'apply' and not data:
                    return self.json(200, tasks.apply(parts[2]))
                raise WebError('invalid_request', 'Укажите только goal для задачи или пустой объект для сохранения.')
            if url.path == '/':
                return self.body(200, (HERE / 'index.html').read_bytes(), 'text/html; charset=utf-8')
            if url.path == '/health':
                return self.json(200, {'status': 'ok', 'mode': tasks.assistant.mode})
            if url.path == '/api/project':
                return self.json(200, tasks.project_info())
            if url.path == '/api/file':
                query = parse_qs(url.query)
                if set(query) != {'path'} or len(query['path']) != 1:
                    raise WebError('invalid_request', 'Укажите один path.')
                return self.json(200, tasks.read_file(query['path'][0]))
            parts = url.path.strip('/').split('/')
            if len(parts) == 3 and parts[:2] == ['api', 'tasks']:
                return self.json(200, tasks.get(parts[2]))
            raise WebError('not_found', 'Страница не найдена.', 404)
        except WebError as exc:
            self.json(exc.status, {'error': {'code': exc.code, 'message': str(exc), **exc.details}})
        except Exception:
            self.json(500, {'error': {'code': 'internal_error', 'message': 'Ошибка сервиса.'}})

    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch(post=True)


def create_server(tasks, port=8034):
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    server.tasks = tasks
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8034)
    parser.add_argument('--project')
    parser.add_argument('--demo', action='store_true')
    args = parser.parse_args()
    project = args.project
    if project is None:
        project = HERE.parents[1] / 'memory/day34-web' / uuid.uuid4().hex[:12]
        project.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(HERE / 'sample', project)
    tasks = WebTasks(FileAssistant(project, mode='demo' if args.demo else 'cloud'))
    server = create_server(tasks, args.port)
    print(f'Project Files: http://127.0.0.1:{server.server_port} ({tasks.assistant.mode})', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
