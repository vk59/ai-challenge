"""Bounded local jobs and replay of the exact reviewed MCP proposals."""
import copy
import json
import sys
import threading
import uuid
from collections import OrderedDict

from assistant import AgentError, FileAssistant, HERE, MCPClient
from workspace import FileError, Workspace


class WebError(Exception):
    def __init__(self, code, message, status=400, **details):
        self.code, self.status, self.details = code, status, details
        super().__init__(message)


class WebTasks:
    def __init__(self, assistant: FileAssistant):
        self.assistant = assistant
        self.gate = threading.Lock()
        self.lock = threading.RLock()
        self.jobs = OrderedDict()

    def get(self, task_id):
        with self.lock:
            if task_id not in self.jobs:
                raise WebError('not_found', 'Задача не найдена.', 404)
            return copy.deepcopy({k: v for k, v in self.jobs[task_id].items() if not k.startswith('_')})

    def start(self, goal):
        if not isinstance(goal, str) or not 1 <= len(goal.strip()) <= 2000:
            raise WebError('invalid_goal', 'Цель должна содержать 1–2000 символов.')
        if not self.gate.acquire(blocking=False):
            raise WebError('busy', 'Ассистент занят, дождитесь завершения.', 503)
        task_id = uuid.uuid4().hex
        with self.lock:
            while len(self.jobs) >= 8:
                self.jobs.popitem(last=False)
            self.jobs[task_id] = dict(id=task_id, goal=goal, state='running', events=[], result=None)
        try:
            threading.Thread(target=self._run, args=(task_id, goal), daemon=True).start()
        except Exception:
            self.gate.release()
            raise
        return self.get(task_id)

    def _event(self, task_id, event):
        with self.lock:
            self.jobs[task_id]['events'].append(event)

    def _run(self, task_id, goal):
        try:
            result = self.assistant.run(goal, on_event=lambda event: self._event(task_id, event), include_plan=True)
            plan = result.pop('_plan')
            with self.lock:
                self.jobs[task_id].update(result=result, _plan=plan,
                                         state='ready' if result['diff'] else 'unchanged')
        except Exception as exc:
            with self.lock:
                self.jobs[task_id].update(state='failed', error={
                    'code': exc.code if isinstance(exc, AgentError) else 'internal_error',
                    'message': str(exc) if isinstance(exc, AgentError) else 'Не удалось выполнить задачу.'})
        finally:
            self.gate.release()

    def apply(self, task_id):
        task = self.get(task_id)
        if task['state'] == 'applied':
            return task
        if task['state'] != 'ready':
            raise WebError('not_ready', 'Сначала подготовьте новый diff.', 409)
        if not self.gate.acquire(blocking=False):
            raise WebError('busy', 'Ассистент занят.', 503)
        applying = False
        try:
            with self.lock:
                job = self.jobs[task_id]
                # Recheck after acquiring the gate: another apply may just have completed.
                if job['state'] == 'applied':
                    return self.get(task_id)
                if job['state'] != 'ready':
                    raise WebError('not_ready', 'Подготовьте новый diff.', 409)
                job['state'] = 'applying'
                plan = copy.deepcopy(job['_plan'])
            command = [sys.executable, str(HERE / 'server.py'), '--project',
                       str(self.assistant.project), '--allow-apply']
            with MCPClient(command) as client:
                def invoke(name, args):
                    raw = client.call_tool(name, args)
                    data = json.loads(raw['content'][0]['text'])
                    if raw.get('isError'):
                        raise WebError(data['code'], data['message'], 409,
                                       applied_files=data.get('applied_files', []))
                    return data

                for entry in plan:
                    try:
                        current = invoke('read_file', {'path': entry['path']})['sha256']
                    except WebError as exc:
                        if exc.code != 'not_found':
                            raise
                        current = None
                    if current != entry['expected_sha256']:
                        raise WebError('conflict', 'Файл изменился после подготовки diff. Задайте цель заново.', 409)
                for entry in plan:
                    invoke('propose_file', {'path': entry['path'], 'content': entry['content']})
                if invoke('show_diff', {})['diff'] != task['result']['diff']:
                    raise WebError('conflict', 'Diff изменился. Подготовьте его заново.', 409)
                applying = True
                saved = invoke('apply_changes', {})
                applying = False
            with self.lock:
                job['result'].update(status='applied', applied_files=saved['applied_files'])
                job['state'] = 'applied'
            return self.get(task_id)
        except Exception as exc:
            error = exc if isinstance(exc, WebError) else WebError(
                'apply_outcome_unknown' if applying else 'apply_failed',
                'Не удалось подтвердить сохранение. Проверьте файлы перед новым запросом.', 409)
            with self.lock:
                job.update(state='conflict' if error.code == 'conflict' else 'failed',
                           error={'code': error.code, 'message': str(error), **error.details})
            raise error
        finally:
            self.gate.release()

    def project_info(self):
        result = Workspace(self.assistant.project).list_files()
        with self.lock:
            saved = {path for job in self.jobs.values() if job.get('result')
                     for path in job['result']['applied_files']}
        result.update(root=str(self.assistant.project), mode=self.assistant.mode,
                      files=sorted(set(result['files']) | saved))
        return result

    def read_file(self, path):
        try:
            return Workspace(self.assistant.project).read_file(path)
        except FileError as exc:
            raise WebError(exc.code, str(exc), 404 if exc.code == 'not_found' else 403) from None
