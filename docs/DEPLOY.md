# CI/CD и деплой на Cybrancee

Хостинг: [Cybrancee Discord Bot Hosting](https://cybrancee.com/discord-bot-hosting) — панель
Pterodactyl, бот запускается из Git-репозитория. Docker и свой сервер не нужны.
Для этого бота хватает самого дешёвого тарифа (512 МБ RAM).

## Как это работает

```
git push в main
   │
   ▼
CI  (.github/workflows/ci.yml)            — также на каждый Pull Request
 ├─ Lint:   ruff check + ruff format --check
 └─ Tests:  pytest на Python 3.10 и 3.12
   │  всё зелёное?
   ▼
Deploy  (.github/workflows/deploy.yml)
 ├─ ветка `production` перематывается на проверенный коммит
 ├─ перезапуск сервера через API панели
 │     └─ при старте сервер сам делает git pull ветки `production`
 │        и pip install -r requirements.txt
 └─ проверка: сервер поднялся и не упал в течение 45 с
```

Сервер на Cybrancee смотрит **только на ветку `production`**, а в неё попадает
лишь код, прошедший CI. Если тесты упали — на хостинге ничего не меняется.

## Настройка (один раз)

### 1. Репозиторий на GitHub

Залейте проект в репозиторий (файл `.env` в Git не попадает — он в `.gitignore`)
и создайте ветку `production`:

```bash
git push origin main
git push origin main:production
```

### 2. Сервер в панели Cybrancee

1. Купите тариф Discord Bot Hosting, язык — **Python**.
2. **Files:** файловый менеджер должен быть **пустым** (иначе Git-интеграция не сработает) —
   удалите файлы-примеры, если они есть.
3. **Startup** — заполните поля (названия в панели могут немного отличаться):

   | Поле | Значение |
   |---|---|
   | Docker Image / Python version | **Python 3.12** (минимум 3.10) |
   | Git Repo Address | `https://github.com/hulumulushnik/BotDs_Voice.git` |
   | Install Branch | `production` |
   | Auto Update | `1` / включено — `git pull` при каждом старте |
   | App py file | `main.py` |
   | Requirements file | `requirements.txt` |
   | Git Username / Git Access Token | только для **приватного** репозитория (см. ниже) |

   Для приватного репозитория: GitHub → Settings → Developer settings →
   **Fine-grained tokens** → доступ только к этому репозиторию, право *Contents: Read-only*.

4. **Settings → Reinstall Server** — сервер склонирует репозиторий.
5. **Files:** создайте файл `.env` в корне по образцу `.env.example` (минимум `DISCORD_TOKEN`).
   `git pull` его не трогает.
6. **Console → Start.** В консоли должно появиться `Бот запущен: ...`.

### 3. API-ключ панели

1. В панели: **Account → API Credentials** → создайте ключ (начинается с `ptlc_`).
2. **ID сервера** — 8 символов из адреса страницы сервера: `https://<панель>/server/`**`1a2b3c4d`**.
3. **Адрес панели** — начало адреса из браузера, например `https://<панель>` (без `/server/...`).

### 4. Секреты в GitHub

**GitHub → репозиторий → Settings → Secrets and variables → Actions → New repository secret:**

| Секрет | Пример |
|---|---|
| `PTERO_PANEL_URL` | `https://panel.example.com` |
| `PTERO_SERVER_ID` | `1a2b3c4d` |
| `PTERO_API_KEY` | `ptlc_...` |

`GITHUB_TOKEN` создаётся автоматически.

### 5. Проверка

Сделайте любой коммит в `main` → вкладка **Actions**: сначала CI, затем *Deploy (Cybrancee)*.
Или запустите деплой вручную: **Actions → Deploy (Cybrancee) → Run workflow**.

## Откат

Сервер умеет только `git pull` вперёд, поэтому откат делается новым коммитом:

```bash
git revert <плохой-коммит>
git push origin main          # пройдёт CI и задеплоится как обычно
```

## Данные бота

Состояние комнат хранится в `data/rooms.json` на сервере. Файл не в Git, поэтому
переживает обновления и перезапуски. **Reinstall Server** стирает все файлы, включая
`.env` и `data/` — перед переустановкой скачайте их через Files (или включите
бэкапы в панели).

## Локальная проверка (то же, что делает CI)

```bash
pip install -r requirements-dev.txt
ruff check . && ruff format --check . && pytest
ruff format .                 # автоформатирование, если упал шаг format
```

## Если деплой упал

| Шаг | Причина и решение |
|---|---|
| Lint | Не отформатирован код → `ruff format .`, закоммитить |
| Tests | Сломан тест — смотрите лог шага |
| Check secrets | Не добавлен один из секретов `PTERO_*` |
| Move `production` branch | Ветка защищена (branch protection) или история `main` переписана force-push'ем |
| Restart server: `401`/`403` | Неверный `PTERO_API_KEY` или ключ от другого аккаунта |
| Restart server: `404` | Неверный `PTERO_SERVER_ID` или `PTERO_PANEL_URL` |
| Wait until bot is up | Бот падает при старте — откройте **Console** в панели (частые причины: нет `.env`, неверный токен, не та версия Python) |
