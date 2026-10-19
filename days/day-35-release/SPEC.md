# День 35: помощник подготовки релиза

Реальная задача: подготовить релиз ai-challenge из существующей Git-истории.
Сервис собирает коммиты, список файлов и ограниченный безопасный diff выбранного
диапазона, передаёт их DeepSeek и создаёт release notes, риски и чеклист.
Пользователь может изменить указание для AI и скачать/сохранить Markdown-пакет.

## Границы
- Реальный локальный Git checkout, неизменяемые base/head SHA; ref задаётся оператором.
- Никакого исполнения кода репозитория, Git push/tag/merge/publish и сборок.
- Все Git-команды read-only с timeout, shell=False; диапазон — ancestor base..head.
- До 100 коммитов, 80 файлов, 60000 символов безопасного diff. Превышение — явная ошибка.
- Чувствительные/бинарные/неподдерживаемые файлы исключаются из diff и показываются
  в evidence как пропуски; текст с распознаваемыми секретами в AI не передаётся.
- AI обязан вернуть JSON с summary, highlights, risks, checklist и commit SHA
  у каждого highlight/risk. SHA проверяются по выбранному диапазону.
- Чеклист — предложения к выполнению. Сервис не заявляет, что тесты/релиз выполнены.
- Сохранение создаёт новый Markdown в фиксированной memory/day35-release/exports;
  путь не контролируется пользователем, повторное сохранение идемпотентно.
- Loopback HTTP :8035, Host/Origin, bounded JSON, одна задача одновременно,
  до 8 задач в памяти; model output выводится через textContent.
- Web: выбор base/head из Git refs, цель, progress, Release notes / Evidence,
  Markdown download и Save. По умолчанию origin/main~1..origin/main (при отсутствии origin/main — HEAD~1..HEAD).
- Прямой DeepSeek, только явный --demo без модели. Никакого тихого fallback.

## Файлы и проверки
release.py: сбор Git evidence, AI JSON validation, Markdown и export.
web.py: HTTP jobs и безопасный API. index.html: пользовательский экран.
smoke.py: реальный Git и AI, save/readback. test_release.py/test_web.py: фикстуры.
README.md/video.md: задача, роль AI, запуск, ограничения и короткий сценарий.
CI: offline и demo; отдельный cloud-smoke с существующим DEEPSEEK_API_KEY.
