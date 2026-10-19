# День 32 — автоматическое AI-ревью PR

GitHub Actions запускает ревью при открытии pull request и при каждом новом push в него. Ассистент получает diff и содержимое изменённых текстовых файлов, находит релевантные места в документации и коде **базовой версии** через RAG и публикует неблокирующий review-комментарий.

## Что делает пайплайн

1. Workflow срабатывает на `opened`, `reopened` и `synchronize`.
2. Checkout берёт base SHA либо SHA проверенного ревьюера, заданный maintainer в repository variable `DAY32_REVIEWER_SHA`; synthetic PR head загружается как Git ref и читается только как данные.
3. `corpus.py` собирает отслеживаемые Markdown, Python и текстовые конфиги из base tree. Секретные пути и файлы, бинарные данные и generated/cache-каталоги исключаются.
4. `review.py` собирает diff и изменённые файлы с head, нарезает документацию и код по структуре с `shared/index.py`, выбирает релевантные фрагменты локальным BM25 (`retrieval.py`), затем вызывает `shared/llm.py` через прямой DeepSeek API.
5. Проверенный Markdown публикуется как review с событием `COMMENT`; он не блокирует merge и не меняет код.

В отзыве всегда есть три раздела: **Потенциальные баги**, **Архитектурные проблемы**, **Рекомендации**. Ассистент должен указывать строки только при наличии точной координаты и явно сообщать, если подтверждённых находок нет. Он не запускает тесты и сборки.

## Настройка GitHub Actions

Добавьте секрет репозитория `DEEPSEEK_API_KEY` в `Settings → Secrets and variables → Actions`. Ключ используется только для генерации ревью. RAG выполняется локально без embedding API: OpenRouter и отдельный ключ эмбеддингов не нужны. При желании задайте переменную `DAY32_DEEPSEEK_MODEL`; по умолчанию используется `deepseek-chat` напрямую. Старое значение `DAY32_REVIEW_MODEL` не используется.

Файл workflow задаёт минимальные права: `contents: read` и `pull-requests: write`. Для создания review GitHub Actions использует свой `GITHUB_TOKEN`; отдельный PAT не нужен.

Workflow может запуститься уже на PR, который впервые добавляет его. По умолчанию он выполняет ревьюер из base SHA. Чтобы проверить установочный PR до merge, maintainer может после проверки кода задать repository variable `DAY32_REVIEWER_SHA` равной полному SHA проверенного коммита с ревьюером. Это фиксированная доверенная версия, а не автоматически выбранный PR head. Корпус RAG и diff по-прежнему используют настоящий base SHA. После merge переменную можно удалить, чтобы брать ревьюер из base. Альтернатива — локальный режим.

## Локальный запуск

Нужны Python 3.10+, Git и `DEEPSEEK_API_KEY` в окружении или в корневом `.env`:

```bash
python3 days/day-32-code-review/review.py \
  --base <BASE_SHA> \
  --head <HEAD_SHA> \
  --output /tmp/day32-review.md
```

Локальный режим только сохраняет отчёт и не требует токена GitHub. Для публикации передайте `GITHUB_TOKEN` в окружении пользователю с правом писать reviews в целевой репозиторий и запустите:

```bash
python3 days/day-32-code-review/review.py \
  --base <BASE_SHA> \
  --head <HEAD_SHA> \
  --output /tmp/day32-review.md \
  --post --repository OWNER/REPOSITORY --pull-number NUMBER
```

BM25 строится в памяти на каждом запуске, embedding-кэш не создаётся. Дифф и суммарный контекст изменённых файлов ограничены 100 000 символами каждый; превышение приводит к явной ошибке, а не к тихой обрезке. Лимит одного файла — 20 000 символов. Если изменённый файл содержит распознаваемый секрет, его diff и содержимое не отправляются модели.

## Pull request из fork

GitHub не передаёт repository secrets workflow-ам из fork PR и ограничивает запись через `GITHUB_TOKEN`. Такой запуск намеренно завершается с понятной ошибкой и не публикует фиктивный отзыв. После проверки PR maintainer может получить head через `refs/pull/NUMBER/head`, запустить локальный режим на проверенных SHA и опубликовать отчёт своим `GITHUB_TOKEN`. PR head никогда не исполняется.

## Проверки

```bash
python3 -m unittest discover -s days/day-32-code-review -p 'test_*.py' -v
python3 -m py_compile days/day-32-code-review/corpus.py days/day-32-code-review/review.py
```

Unit-тесты создают временный Git-репозиторий и мокают LLM/GitHub API; реальные ключи и сеть им не нужны.
