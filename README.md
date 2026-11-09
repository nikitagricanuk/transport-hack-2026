# УДС·Редактор — Транспортный хак 2026

Визуальный редактор улично-дорожной сети: загрузить `.osm`, исправить граф (узлы, рёбра, направления,
полосы и манёвры, перекрёстки, мосты, светофоры, переходы) и сохранить **один `.osm`**, который без ручной
доработки проходит MatSim → SUMO → Webots на сервере организаторов.

## Быстрый старт (Docker)

Одна команда из корня репозитория — Compose сам соберёт образ, если его ещё нет:

```bash
docker compose up
```

Откройте http://127.0.0.1:8000. Файлы из папки `data/` (например, `example.osm`) появятся на стартовом экране.
Чтобы пересобрать образ после изменений в коде: `docker compose up --build`.

Образ по умолчанию **без SUMO** — так он остаётся небольшим, и именно его публикует CI.
На диске это около 262 MB (`docker images`); вариант с SUMO — около 587 MB, на сервер он не уезжает.
Кнопка «SUMO-проверка» в локальном контейнере:

```bash
WITH_SUMO=1 docker compose up --build
```

Контейнер слушает только localhost, процесс работает от пользователя `app` (uid 10001), healthcheck — `GET /api/health`.

## Разработка

Бэкенд (Python 3.11+):

```bash
cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt     # Linux/macOS: .venv/bin/pip
.venv/Scripts/python -m uvicorn app.main:app --reload
.venv/Scripts/python -m pytest -q
```

Фронтенд (Node 20+), в другом терминале:

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173, /api проксируется на :8000
```

Для кнопки «SUMO-проверка» установите в venv `pip install eclipse-sumo` — тогда экспортированный файл
прогоняется через `netconvert` локально, без ожидания сервера.

## CI и деплой

Workflow [`.github/workflows/ci.yml`](.github/workflows/ci.yml) на push и pull request:

- бэкенд: `ruff check` и `pytest` (кэш pip);
- фронтенд: `npm ci`, `npm run lint`, `npm run build` (кэш npm);
- сборка Docker-образа (кэш слоёв GitHub Actions).

Push в `main` публикует образ без SUMO (`WITH_SUMO=0`) в GHCR:

- `ghcr.io/nikitagricanuk/transport-hack-2026:latest`
- `ghcr.io/nikitagricanuk/transport-hack-2026:<полный sha коммита>`

Для публикации workflow использует `GITHUB_TOKEN` с правом `packages: write`. Отдельный секрет для push не нужен.

Job `deploy` запускается только после успешной сборки, и только если событие — push в `main` или `workflow_dispatch` ветки `main`. На pull request job пропускается. `workflow_dispatch` заново образ не публикует: на сервер уезжает уже запушенный `:latest`.

На сервере (`deploy/remote-deploy.sh`):

1. Каталог `/opt/transport-hack`. Если его нельзя создать или в него нельзя писать — `~/transport-hack` пользователя `deploy`.
2. В каталог записывается [`deploy/docker-compose.prod.yml`](deploy/docker-compose.prod.yml): образ GHCR, порт только `127.0.0.1:8095`, `restart: unless-stopped`, healthcheck, логи `json-file` (max-size 10m, max-file 3), именованный том `transport-hack-data` на `/data`. Имя контейнера — `transport-hack-editor`.
3. `docker login ghcr.io` пишет токен только во временный `DOCKER_CONFIG` (`mktemp -d`, режим 700). Пароль — секрет `GHCR_TOKEN`, если он задан, иначе `GITHUB_TOKEN` этого job. `docker compose pull` использует этот каталог. Ловушка `EXIT` на любом выходе, в том числе после ошибки `pull`, `up` или `prune`, делает `docker logout` и удаляет каталог. В `~/.docker/config.json` токен не попадает.
4. `docker compose up -d --pull never` поднимает уже скачанный образ и в реестр больше не ходит, затем `docker image prune -f`.
5. `curl` `http://127.0.0.1:8095/api/health` до 15 раз с паузой 3 секунды. В ответе должно быть `"ok": true`, иначе job падает.

Nginx на сервере уже терминирует HTTPS для https://thack.nikitagricanuk.ru и проксирует на `127.0.0.1:8095`.

Сервер — RHEL 10, SELinux enforcing, Docker 29 и Compose v5. Пользователь SSH должен запускать `docker` без пароля (группа `docker`). В production нет bind-mount, поэтому `:Z` не нужен. Если позже примонтируете каталог с хоста, добавьте суффикс `:Z`. Чужие контейнеры не используются: свой проект `transport-hack`, свой контейнер, порт и том.

### Секреты

В репозитории уже есть (Settings → Secrets and variables → Actions → Secrets):

| Секрет | Назначение |
| --- | --- |
| `DEPLOY_SSH_KEY` | приватный ключ пользователя `deploy` |
| `DEPLOY_KNOWN_HOSTS` | строка `known_hosts` для этого хоста. Проверка ключа строгая: `StrictHostKeyChecking=yes`. `StrictHostKeyChecking=no` не используется |

Необязательный секрет:

| Секрет | Назначение |
| --- | --- |
| `GHCR_TOKEN` | PAT с `read:packages`, если pull с сервера должен идти не от `GITHUB_TOKEN` |

`GITHUB_TOKEN` Actions создаёт сам.

### Переменные

Хост и пользователь — не секреты. В workflow стоят значения по умолчанию, их перекрывают repository variables:

| Variable | По умолчанию |
| --- | --- |
| `DEPLOY_HOST` | `novsrv.nodes.nikitagricanuk.ru` |
| `DEPLOY_USER` | `deploy` |

Порт SSH — 22. Variables можно не заводить, пока сервер тот же. Если смените хост, обновите и `DEPLOY_KNOWN_HOSTS`: несовпавший ключ оборвёт deploy.

### Приватный или публичный пакет GHCR

Пакет **может оставаться приватным**. Deploy логинится в `ghcr.io` перед `docker compose pull`, так что открывать пакет не обязательно. Первый push из `main` создаёт приватный `ghcr.io/nikitagricanuk/transport-hack-2026`.

Публичным (Package settings → Change visibility → Public) его стоит делать только если нужен `docker pull` без токена. Этому workflow это не требуется. Если pull с `GITHUB_TOKEN` вернёт 403, задайте `GHCR_TOKEN` (PAT с `read:packages`) либо откройте пакет.

## Что умеет

| Функция | Где |
| --- | --- |
| Импорт `.osm`, отображение в 2D на подложке OSM | `StartScreen`, `MapView` |
| Экспорт без потерь: порядок, атрибуты, порядок тегов, `<bounds>`/`<meta>` сохраняются | `backend/app/osm_model.py` |
| Правки: теги, полосы (`lanes`, `turn:lanes` автоматически), узлы, рёбра, разделение, склейка, разворот | `backend/app/edits.py` |
| Undo/redo для любой операции, шаблон дороги — один шаг | `History`, `apply_ops` |
| Горячие клавиши: V W N S M R T P, 1–4 шаблоны, Ctrl+Z / Ctrl+Shift+Z, Delete, Esc | `App.tsx` |
| Проверки: связность, разорванные/склеенные перекрёстки, тупики на oneway, полосы, мосты без layer, светофоры | `backend/app/validation.py` |
| Автоисправления из панели проблем | поле `fix` у проблемы |
| Локальный `netconvert` | `backend/app/sumo_check.py` |

## Как устроено

```
frontend (React + MapLibre)  ──HTTP──▶  backend (FastAPI)
  карта, инструменты, панели             OsmDocument (модель без потерь)
                                         History (снимки до/после → undo/redo)
                                         validate()  → проблемы + fix-операции
                                         to_xml()    → team.osm
```

Каждая правка — POST `/api/documents/{id}/ops` c `{op, args}`; ответ содержит новое состояние карты.
Новые объекты получают отрицательные id и при экспорте перенумеровываются в свободные положительные.

## API

| Метод | Путь | Назначение |
| --- | --- | --- |
| POST | `/api/documents` | загрузить `.osm` (multipart `file`) |
| POST | `/api/samples/{name}/open` | открыть файл из `data/` |
| POST | `/api/documents/{id}/ops` | одна операция |
| POST | `/api/documents/{id}/batch` | несколько операций одним шагом отмены |
| POST | `/api/documents/{id}/undo`, `/redo` | отмена / повтор |
| GET | `/api/documents/{id}/validate` | проверка графа |
| GET | `/api/documents/{id}/export?filename=team1` | скачать `.osm` |
| POST | `/api/documents/{id}/check/sumo` | прогон `netconvert` |

Операции: `set_tags`, `set_lanes`, `move_node`, `add_node`, `insert_node`, `add_way`, `delete`,
`split_way`, `merge_nodes`, `connect_endpoint`, `reverse_way`.

## Сдача

Имя файла должно совпадать с названием команды — поле рядом с кнопкой «Экспорт .osm».
Загрузите файл на сервер проверки с токеном команды и дождитесь трёх видео.
