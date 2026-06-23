# Telegram Market — бот + MiniApp

Магазин цифровых ключей: aiogram 3 + FastAPI + SQLite + статичный MiniApp.
Оплата ⭐ Telegram Stars и 💵 криптой через CryptoBot. Промокоды, автовыдача
ключей, админ-команды, верификация WebApp и вебхуков.

## Структура

```
.
├── bot.py             # aiogram-хэндлеры (/start, /admin..., Stars-чекаут)
├── server.py          # FastAPI: /api/* для MiniApp, /webhook/cryptopay
├── main.py            # запускает бота + uvicorn в одном процессе
├── db.py              # SQLite, ВКЛЮЧАЯ атомарную выдачу ключа
├── crypto_pay.py      # Crypto Pay API клиент + HMAC проверка вебхука
├── webapp_auth.py     # валидация Telegram WebApp initData
├── config.py          # загрузка env
├── miniapp/
│   ├── index.html
│   ├── style.css      # тема подстраивается под клиент Telegram
│   └── app.js
├── requirements.txt
├── Dockerfile
├── fly.toml           # Fly.io
└── render.yaml        # Render.com
```

## Шаг 1. Создать ботов и токены

1. **BotFather** → создай бота, забери `BOT_TOKEN`.
2. **BotFather** → `/setdomain` → укажи домен MiniApp (например `your-app.pages.dev`).
3. **BotFather** → `/newapp` → к боту прикрепи Mini App, укажи URL фронта.
4. **@CryptoBot** → раздел *Crypto Pay* → создай приложение → забери токен в `CRYPTO_PAY_TOKEN`.
5. **@userinfobot** → узнай свой `user_id`, положи в `ADMIN_IDS`.

## Шаг 2. Локальный запуск

```bash
cp .env.example .env   # заполни
python -m venv .venv && .venv\Scripts\activate    # Windows
pip install -r requirements.txt
python main.py
```

`BASE_URL` локально — это адрес туннеля (ngrok/cloudflared), на который CryptoBot будет
слать вебхук. Иначе вебхук просто не дойдёт.

```bash
cloudflared tunnel --url http://localhost:8080
# -> https://something.trycloudflare.com  -> положи в BASE_URL
```

Затем в кабинете CryptoBot укажи webhook URL:
`https://<твой_BASE_URL>/webhook/cryptopay`

## Шаг 3. Наполнение

В боте от админа:

```
/addcat 🎯 Standoff 2
/addproduct 1|VIP 30 дней|Полный доступ ко всем функциям|30 дней|499|350
/addkeys 1
ABCD-1111
ABCD-2222
ABCD-3333
/addpromo SUMMER 15 100 7
```

`addpromo CODE pct max_uses days_ttl` — например `SUMMER 15 100 7` = скидка 15%,
до 100 применений, действует 7 дней. `days_ttl=0` — бессрочно.

## Шаг 4. Бесплатный деплой (несколько вариантов)

Цель: бот, который работает 24/7, и MiniApp, доступный по HTTPS, бесплатно.

### Вариант A — самый надёжный (рекомендуется): Fly.io + Cloudflare Pages

Fly даёт 3 крошечных VPS бесплатно с постоянным диском (как раз для SQLite),
а Cloudflare Pages — бесплатный хостинг статики с глобальной CDN.

```bash
# 1) backend на Fly
curl -L https://fly.io/install.sh | sh   # или scoop install flyctl
fly auth signup                          # бесплатно
fly launch --no-deploy                   # принимай fly.toml как есть
fly volumes create market_data --size 1 --region fra
fly secrets set BOT_TOKEN=... CRYPTO_PAY_TOKEN=... \
                ADMIN_IDS=... WEBAPP_URL=https://your.pages.dev/ \
                BASE_URL=https://tg-market.fly.dev
fly deploy
```

```bash
# 2) MiniApp на Cloudflare Pages
# залей содержимое папки miniapp/ как репо
# в Pages: New project -> Connect Git -> root /miniapp
# build command: (пусто), output dir: /
```

В `miniapp/index.html` перед `app.js` добавь, если фронт и API на разных доменах:
```html
<meta name="api-base" content="https://tg-market.fly.dev">
```

В BotFather пропиши домен MiniApp: команда `/setdomain` → `your.pages.dev`.

### Вариант B — Oracle Cloud Free Tier (вечно бесплатный VPS)

ARM Ampere (4 vCPU, 24GB RAM), сам по себе бесплатен навсегда:

```bash
# на VPS
sudo apt update && sudo apt install -y docker.io
git clone <твой репо>
cd <repo>
docker build -t tgmarket .
docker run -d --name tgmarket --restart unless-stopped \
  -p 8080:8080 \
  -v /opt/tgmarket:/data \
  -e DB_PATH=/data/market.db \
  -e BOT_TOKEN=... -e CRYPTO_PAY_TOKEN=... -e ADMIN_IDS=... \
  -e WEBAPP_URL=https://your.pages.dev/ \
  -e BASE_URL=https://api.yourdomain.tld \
  tgmarket
```

Перед ним — Caddy для HTTPS бесплатно:

```caddyfile
api.yourdomain.tld {
  reverse_proxy localhost:8080
}
```

### Вариант C — Railway/Render free tier (с оговорками)

- **Render free** — usable, но контейнер засыпает при простое. Бот пропустит апдейты.
  Можно «пинговать» `/healthz` каждые 5 минут через GitHub Action cron — будет жить.
- **Railway** — даёт $5 кредитов в месяц; на этом проекте хватает.

### Вариант D — Полностью без VPS (только статика + Telegram webhook)

Если очень не хочется поднимать сервер 24/7 — можно перевести бота на webhook
и хостить FastAPI на бесплатном Cloudflare Workers / Vercel Serverless. Это
больше переделок (Workers не умеет в aiosqlite напрямую — нужен D1 или Turso).
Если интересно — попроси переделать.

## Шаг 5. Где статика MiniApp?

Два варианта:
1. **Тот же бэкенд** — `server.py` монтирует `/miniapp/*` со статикой.
   В этом случае `WEBAPP_URL=https://tg-market.fly.dev/miniapp/`.
2. **Cloudflare Pages / GitHub Pages / Netlify** — лучше для холодных стартов
   и CDN. Тогда в `index.html` добавь `<meta name="api-base">` с URL бэкенда.

## Безопасность — что я уже закрыл

| Угроза                              | Меры                                                            |
|-------------------------------------|-----------------------------------------------------------------|
| Подделка initData (любой → любой)   | HMAC по BOT_TOKEN + проверка `auth_date < 1 час`                |
| Подделка вебхука CryptoBot          | HMAC-SHA256 по SHA256(token) сравнивается с заголовком          |
| Цена в теле запроса от клиента      | Цена и скидка считаются на сервере, клиент шлёт только product_id|
| Двойная выдача ключа                | `BEGIN IMMEDIATE` + claim ключа в одной транзакции              |
| Повторная обработка вебхука         | `fulfill_order` идемпотентен: повторный вызов отдаёт тот же key |
| Replay чужого `successful_payment`  | проверяю `currency=='XTR'` и `amount == order.amount` в pre_checkout |
| Спам/брутфорс API                   | Per-user токен-бакет (20 req/мин)                               |
| SQL-инъекции                        | только параметризованные запросы                                |
| XSS в MiniApp                       | `escapeHtml()` для всех вставок                                 |
| Заниженный платёж в крипте          | сравниваю фактический `amount` с ожидаемым перед выдачей        |
| Промокод-обход                      | повторно валидируется на сервере при чекауте, инкремент в той же атомарной транзакции |
| Доступ к /admin                     | проверка `from_user.id in ADMIN_IDS` в каждом хэндлере          |
| OOM от больших полей                | Pydantic + maxlength + clamp `expires_in`                       |

## Что осознанно НЕ закрыто

- **Возвраты Stars** реализуй вручную через `refundStarPayment` в личной поддержке.
- **Антифрод по картам** не нужен — мы вообще не принимаем карты.
- **Мульти-инстанс** — SQLite не масштабируется на несколько серверов. Если будут
  тысячи RPS, мигрируй на Postgres (Neon, Supabase free).

## Тест-чеклист перед продакшеном

1. `python main.py` локально, открой `http://localhost:8080/healthz` → `{"ok":true}`.
2. Открой MiniApp через бота — должен подгрузиться список категорий.
3. Купи звёздами — пройди весь флоу до получения ключа в чате.
4. Купи криптой через testnet (`CRYPTO_PAY_API=https://testnet-pay.crypt.bot`).
5. Подделай initData (поменяй hash) → 401.
6. Подделай тело вебхука → 401.
7. Купи два ключа подряд, когда в наличии один — второй должен попасть в статус
   `paid` без ключа (а не выдать невалидный).
8. Используй один и тот же промокод одновременно из двух чатов на пределе
   `max_uses` — не должен превысить лимит.

## Лицензия

MIT. Делай с этим что хочешь.
