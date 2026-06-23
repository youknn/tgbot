"""FastAPI app: MiniApp HTTP API + CryptoBot webhook.

Auth model:
  • Every /api/* request must include header `X-Init-Data` with raw Telegram
    WebApp initData. We HMAC-validate it against BOT_TOKEN — without it we
    refuse to act on behalf of any user.
  • /webhook/cryptopay verifies the `crypto-pay-api-signature` header before
    touching state.
  • All prices are looked up server-side from products table — client cannot
    spoof a price by mutating the request body."""
import hmac
import logging
import time
from typing import Optional


def _safe_eq(a: str, b: str) -> bool:
    return hmac.compare_digest((a or "").encode(), (b or "").encode())

from aiogram import Bot, Dispatcher
from aiogram.types import Update
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import db
from bot import create_stars_invoice_link, send_stars_invoice
from config import Config
from crypto_pay import CryptoPay
from webapp_auth import validate_init_data

log = logging.getLogger(__name__)


class CreateInvoice(BaseModel):
    product_id: int = Field(..., gt=0)
    method: str = Field(..., pattern="^(stars|crypto)$")
    promo: Optional[str] = Field(None, max_length=64)


class PromoCheck(BaseModel):
    code: str = Field(..., min_length=1, max_length=64)
    product_id: int = Field(..., gt=0)


# trivial per-user token bucket
_BUCKET: dict[int, list[float]] = {}


def rate_limit(user_id: int, n: int = 20, per_sec: int = 60) -> bool:
    now = time.time()
    arr = _BUCKET.setdefault(user_id, [])
    arr[:] = [t for t in arr if now - t < per_sec]
    if len(arr) >= n:
        return False
    arr.append(now)
    return True


def make_app(cfg: Config, conn, bot: Bot, crypto: CryptoPay, dp: Dispatcher | None = None) -> FastAPI:
    app = FastAPI(title="Market API", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    async def require_user(init_data: str) -> dict:
        user = validate_init_data(init_data, cfg.bot_token)
        if not user:
            raise HTTPException(401, "bad initData")
        if await db.is_banned(conn, user["id"]):
            raise HTTPException(403, "banned")
        if not rate_limit(user["id"]):
            raise HTTPException(429, "slow down")
        await db.upsert_user(conn, user["id"], user.get("username"), user.get("first_name"))
        return user

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    @app.get("/api/bootstrap")
    async def bootstrap(x_init_data: str = Header(default="")):
        user = await require_user(x_init_data)
        cats = await db.list_categories(conn)
        prods = await db.list_products(conn)
        return {
            "user": {"id": user["id"], "first_name": user.get("first_name", "")},
            "categories": cats,
            "products": prods,
        }

    @app.post("/api/promo/check")
    async def promo_check(body: PromoCheck, x_init_data: str = Header(default="")):
        await require_user(x_init_data)
        p = await db.get_valid_promo(conn, body.code)
        if not p:
            return {"ok": False, "reason": "invalid"}
        product = await db.get_product(conn, body.product_id)
        if not product or not product["active"]:
            raise HTTPException(404, "product not found")
        price_rub = product["price_rub"]
        price_stars = product["price_stars"]
        discounted_rub = round(price_rub * (100 - p["discount_pct"]) / 100)
        discounted_stars = max(1, round(price_stars * (100 - p["discount_pct"]) / 100))
        return {
            "ok": True,
            "discount_pct": p["discount_pct"],
            "price_rub": discounted_rub,
            "price_stars": discounted_stars,
        }

    @app.post("/api/checkout")
    async def checkout(body: CreateInvoice, x_init_data: str = Header(default="")):
        user = await require_user(x_init_data)
        product = await db.get_product(conn, body.product_id)
        if not product or not product["active"]:
            raise HTTPException(404, "product not found")
        # check stock now (best effort; final claim happens on payment)
        stock = await db.stock_for(conn, body.product_id)
        if stock <= 0:
            raise HTTPException(409, "out of stock")

        promo_code: Optional[str] = None
        discount_pct = 0
        if body.promo:
            p = await db.get_valid_promo(conn, body.promo)
            if p:
                promo_code = p["code"]
                discount_pct = p["discount_pct"]

        if body.method == "stars":
            price = max(1, round(product["price_stars"] * (100 - discount_pct) / 100))
            order_id = await db.create_order(
                conn, user["id"], body.product_id, promo_code, "XTR", price, "stars",
            )
            try:
                invoice_url = await create_stars_invoice_link(
                    bot, order_id,
                    title=product["name"],
                    description=(product["description"] or product["name"])[:255],
                    amount_stars=price,
                )
            except Exception as e:
                log.exception("createInvoiceLink failed")
                raise HTTPException(500, f"telegram error: {e}")
            return {
                "ok": True, "method": "stars",
                "order_id": order_id, "amount": price,
                "invoice_url": invoice_url,
            }

        # crypto
        usdt_amount = round(product["price_rub"] / 100 / cfg.rub_usdt_rate
                            * (100 - discount_pct) / 100, 2)
        if usdt_amount < 0.10:
            usdt_amount = 0.10  # CryptoBot minimum
        # store in micro USDT to keep integer math clean
        amount_micro = int(round(usdt_amount * 1_000_000))
        order_id = await db.create_order(
            conn, user["id"], body.product_id, promo_code, "USDT",
            amount_micro, "cryptobot",
        )
        invoice = await crypto.create_invoice(
            amount=usdt_amount,
            asset="USDT",
            description=f"{product['name']} #{order_id}",
            payload=f"order:{order_id}",
            expires_in=1800,
        )
        await db.set_order_provider_ref(conn, order_id, str(invoice["invoice_id"]))
        return {
            "ok": True,
            "method": "crypto",
            "order_id": order_id,
            "pay_url": invoice.get("bot_invoice_url") or invoice["pay_url"],
            "mini_app_invoice_url": invoice.get("mini_app_invoice_url"),
            "amount_usdt": usdt_amount,
        }

    @app.get("/api/orders/my")
    async def orders_my(x_init_data: str = Header(default="")):
        user = await require_user(x_init_data)
        return {"orders": await db.my_orders(conn, user["id"])}

    @app.post("/webhook/cryptopay")
    async def webhook_cryptopay(request: Request):
        body = await request.body()
        sig = request.headers.get("crypto-pay-api-signature")
        if not crypto.verify_webhook(body, sig):
            log.warning("rejected CryptoPay webhook: bad signature")
            raise HTTPException(401, "bad signature")
        payload = await request.json()
        if payload.get("update_type") != "invoice_paid":
            return {"ok": True}
        inv = payload.get("payload") or {}
        invoice_id = str(inv.get("invoice_id", ""))
        if not invoice_id:
            return {"ok": True}
        # Custom payload we set during createInvoice: "order:<id>"
        custom = (inv.get("payload") or "")
        order_id = 0
        if custom.startswith("order:"):
            try:
                order_id = int(custom.split(":", 1)[1])
            except ValueError:
                order_id = 0
        if not order_id:
            ord_row = await db.find_order_by_ref(conn, "cryptobot", invoice_id)
            if ord_row:
                order_id = ord_row["id"]
        if not order_id:
            log.warning("paid invoice without matching order: %s", invoice_id)
            return {"ok": True}

        existing = await db.get_order(conn, order_id)
        if not existing:
            return {"ok": True}
        # Cross-check amount/asset so a hijacked webhook can't promote a 1¢ payment
        if str(inv.get("asset")) != "USDT":
            log.warning("wrong asset for order %s: %s", order_id, inv.get("asset"))
            return {"ok": True}
        try:
            paid = float(inv.get("amount", "0"))
        except (TypeError, ValueError):
            paid = 0.0
        expected = existing["amount"] / 1_000_000
        if paid + 1e-6 < expected:
            log.warning("underpaid: order %s expected %s got %s", order_id, expected, paid)
            return {"ok": True}

        key = await db.fulfill_order(conn, order_id)
        if key:
            try:
                await bot.send_message(
                    existing["user_id"],
                    f"✅ <b>Оплата получена.</b>\nВаш ключ:\n<code>{key}</code>",
                )
            except Exception:
                log.exception("notify user failed")
        else:
            try:
                await bot.send_message(
                    existing["user_id"],
                    "Оплата получена, но ключей временно нет. Администратор выдаст вручную.",
                )
            except Exception:
                pass
        return JSONResponse({"ok": True})

    if cfg.webhook_mode and dp is not None:
        @app.post("/tg-webhook/{secret}")
        async def tg_webhook(secret: str, request: Request):
            # Belt-and-braces: secret in path AND in Telegram header.
            if not _safe_eq(secret, cfg.webhook_secret):
                raise HTTPException(404)
            header_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
            if not _safe_eq(header_secret, cfg.webhook_secret):
                raise HTTPException(401, "bad secret")
            body = await request.body()
            try:
                update = Update.model_validate_json(body)
            except Exception:
                raise HTTPException(400, "bad update")
            await dp.feed_update(bot, update)
            return {"ok": True}

    # Serve MiniApp statics from /miniapp/* if files are co-located. Optional —
    # in production prefer Cloudflare Pages / GitHub Pages for the frontend.
    try:
        app.mount("/miniapp", StaticFiles(directory="miniapp", html=True), name="miniapp")
    except RuntimeError:
        pass

    return app
