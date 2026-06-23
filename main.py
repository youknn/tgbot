"""Single-process entrypoint.

Two modes selected by WEBHOOK_MODE env:
  • polling (default, for local dev): aiogram long-polling + uvicorn together.
  • webhook (for Render / any platform that goes to sleep): we register a
    Telegram webhook and only run uvicorn — Telegram itself wakes the dyno
    by hitting /tg-webhook/<secret> on each update.
"""
import asyncio
import logging

import aiosqlite
import uvicorn

import db
from bot import build_dispatcher, make_bot
from config import load_config
from crypto_pay import CryptoPay
from server import make_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
)
log = logging.getLogger("main")


async def main() -> None:
    cfg = load_config()
    await db.init_db(cfg.db_path)
    conn = await aiosqlite.connect(cfg.db_path, isolation_level=None)
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    await conn.execute("PRAGMA busy_timeout=5000")

    bot = await make_bot(cfg)
    crypto = CryptoPay(cfg.crypto_pay_token, cfg.crypto_pay_api)
    dp = build_dispatcher(cfg, conn)

    app = make_app(cfg, conn, bot, crypto, dp=dp)

    uconfig = uvicorn.Config(app, host=cfg.host, port=cfg.port,
                             log_level="info", access_log=False)
    server = uvicorn.Server(uconfig)

    if cfg.webhook_mode:
        webhook_url = f"{cfg.base_url}/tg-webhook/{cfg.webhook_secret}"
        await bot.delete_webhook(drop_pending_updates=True)
        await bot.set_webhook(
            url=webhook_url,
            secret_token=cfg.webhook_secret,
            allowed_updates=dp.resolve_used_update_types(),
            drop_pending_updates=True,
        )
        log.info("webhook mode: %s", webhook_url)
        await server.serve()
        return

    # polling mode (local dev)
    await bot.delete_webhook(drop_pending_updates=True)
    log.info("polling mode + http server on %s:%s", cfg.host, cfg.port)

    polling_task = asyncio.create_task(dp.start_polling(bot, handle_signals=False))
    server_task = asyncio.create_task(server.serve())

    done, pending = await asyncio.wait(
        [polling_task, server_task], return_when=asyncio.FIRST_EXCEPTION,
    )
    for t in pending:
        t.cancel()
    for t in done:
        if exc := t.exception():
            raise exc


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
