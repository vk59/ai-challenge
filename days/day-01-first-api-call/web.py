#!/usr/bin/env python3
"""День 1: тот же запрос к LLM, но через простой веб-интерфейс.

    python web.py            # http://127.0.0.1:8000
    python web.py 9000       # другой порт

Веб-сервер на http.server из стандартной библиотеки — Flask сюда не нужен,
задача ровно одна: поле ввода, кнопка, ответ модели.
"""

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from llm import LLMError, ask

PAGE = """<!doctype html>
<html lang="ru">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>День 1 — запрос к LLM</title>
<style>
  :root { color-scheme: dark; }
  body { margin: 0; padding: 2rem 1rem; background: #14161a; color: #e8eaed;
         font: 16px/1.6 system-ui, -apple-system, sans-serif; }
  main { max-width: 720px; margin: 0 auto; }
  h1 { font-size: 1.25rem; margin: 0 0 1.25rem; font-weight: 600; }
  textarea { width: 100%; box-sizing: border-box; min-height: 110px; padding: .75rem;
             border-radius: 10px; border: 1px solid #33373d; background: #1c1f24;
             color: inherit; font: inherit; resize: vertical; }
  button { margin-top: .75rem; padding: .6rem 1.4rem; border: 0; border-radius: 10px;
           background: #4d7cfe; color: #fff; font: inherit; font-weight: 600; cursor: pointer; }
  button:disabled { opacity: .5; cursor: default; }
  #answer { margin-top: 1.5rem; padding: 1rem; border-radius: 10px; background: #1c1f24;
            border: 1px solid #33373d; white-space: pre-wrap; min-height: 3rem; }
  #answer.error { border-color: #a33; color: #ff9b9b; }
  .hint { color: #8b9099; font-size: .85rem; margin-top: .5rem; }
</style>
<main>
  <h1>День 1 — первый запрос к LLM через API</h1>
  <textarea id="prompt" placeholder="Спроси что-нибудь у модели..." autofocus></textarea>
  <div><button id="send">Отправить</button></div>
  <div class="hint">Ctrl/Cmd + Enter — тоже отправляет</div>
  <div id="answer"></div>
</main>
<script>
  const promptEl = document.getElementById('prompt');
  const answerEl = document.getElementById('answer');
  const sendEl = document.getElementById('send');

  async function send() {
    const prompt = promptEl.value.trim();
    if (!prompt) return;
    sendEl.disabled = true;
    answerEl.className = '';
    answerEl.textContent = 'Думаю...';
    try {
      const res = await fetch('/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt })
      });
      const data = await res.json();
      answerEl.textContent = data.answer ?? data.error;
      answerEl.className = data.error ? 'error' : '';
    } catch (e) {
      answerEl.textContent = 'Не удалось связаться с сервером: ' + e;
      answerEl.className = 'error';
    } finally {
      sendEl.disabled = false;
    }
  }

  sendEl.addEventListener('click', send);
  promptEl.addEventListener('keydown', e => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') send();
  });
</script>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/":
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")

    def do_POST(self):
        if self.path != "/ask":
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return

        length = int(self.headers.get("Content-Length") or 0)
        try:
            prompt = json.loads(self.rfile.read(length) or b"{}").get("prompt", "").strip()
        except json.JSONDecodeError:
            self._send_json(400, {"error": "Тело запроса — не JSON"})
            return

        if not prompt:
            self._send_json(400, {"error": "Пустой запрос"})
            return

        try:
            self._send_json(200, {"answer": ask(prompt)})
        except LLMError as exc:
            self._send_json(200, {"error": str(exc)})

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def log_message(self, fmt, *args):  # чуть тише стандартного лога
        sys.stderr.write(f"  {self.command} {self.path} — {args[1]}\n")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    print(f"Открой http://127.0.0.1:{port}  (Ctrl+C — остановить)")
    try:
        ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
