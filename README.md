# УДС·Редактор — Транспортный хак 2026

Визуальный редактор улично-дорожной сети: загрузить `.osm`, исправить граф (узлы, рёбра, направления,
полосы и манёвры, перекрёстки, мосты, светофоры, переходы) и сохранить **один `.osm`**, который без ручной
доработки проходит MatSim → SUMO → Webots на сервере организаторов.

## Быстрый старт (Docker)

```bash
docker compose up --build
```

Откройте http://localhost:8000. Файлы из папки `data/` (например, `example.osm`) появятся на стартовом экране.
Образ без SUMO (меньше и быстрее собирается): `docker compose build --build-arg WITH_SUMO=0`.

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
