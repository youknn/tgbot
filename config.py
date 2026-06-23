import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    bot_token: str
    crypto_pay_token: str
    crypto_pay_api: str
    admin_ids: list[int]
    webapp_url: str
    base_url: str
    host: str
    port: int
    db_path: str
    rub_usdt_rate: float
    webhook_mode: bool
    webhook_secret: str


def load_config() -> Config:
    webhook_mode = os.environ.get("WEBHOOK_MODE", "0") in ("1", "true", "yes")
    webhook_secret = os.environ.get("WEBHOOK_SECRET", "")
    if webhook_mode and not webhook_secret:
        raise RuntimeError("WEBHOOK_MODE=1 requires WEBHOOK_SECRET to be set")
    return Config(
        bot_token=os.environ["BOT_TOKEN"],
        crypto_pay_token=os.environ["CRYPTO_PAY_TOKEN"],
        crypto_pay_api=os.environ.get("CRYPTO_PAY_API", "https://pay.crypt.bot"),
        admin_ids=[int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()],
        webapp_url=os.environ["WEBAPP_URL"],
        base_url=os.environ["BASE_URL"].rstrip("/"),
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8080")),
        db_path=os.environ.get("DB_PATH", "market.db"),
        rub_usdt_rate=float(os.environ.get("RUB_USDT_RATE", "95")),
        webhook_mode=webhook_mode,
        webhook_secret=webhook_secret,
    )
