"""aiogram 3 bot: /start, menu, admin commands, Stars checkout."""
import asyncio
import logging
import time

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton,
    LabeledPrice, Message, PreCheckoutQuery, ReplyKeyboardMarkup, WebAppInfo,
)

import db
from config import Config

log = logging.getLogger(__name__)
router = Router()


def main_kb(webapp_url: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🛒 Открыть магазин", web_app=WebAppInfo(url=webapp_url))],
            [KeyboardButton(text="🧾 Мои покупки"), KeyboardButton(text="🎁 Промокод")],
            [KeyboardButton(text="🆘 Поддержка")],
        ],
        resize_keyboard=True,
    )


HELLO = (
    "<b>Добро пожаловать в магазин 🎯</b>\n\n"
    "Здесь ты можешь купить ключи мгновенно — оплата звёздами Telegram ⭐ или криптой через CryptoBot.\n\n"
    "Открой <b>магазин</b> ниже, выбирай товар и забирай ключ автоматически."
)


@router.message(CommandStart())
async def on_start(m: Message, cfg: Config, conn):
    await db.upsert_user(conn, m.from_user.id, m.from_user.username, m.from_user.first_name)
    if await db.is_banned(conn, m.from_user.id):
        await m.answer("Вы заблокированы.")
        return
    await m.answer(HELLO, reply_markup=main_kb(cfg.webapp_url))


@router.message(F.text == "🧾 Мои покупки")
async def on_orders(m: Message, conn):
    orders = await db.my_orders(conn, m.from_user.id)
    if not orders:
        await m.answer("Покупок пока нет. Открой магазин и выбери товар 👇")
        return
    parts = ["<b>Ваши покупки:</b>"]
    for o in orders[:15]:
        date = time.strftime("%d.%m.%Y %H:%M", time.gmtime(o["created_at"]))
        status = {
            "pending": "⏳ ожидает оплаты",
            "paid": "❗ оплачено (нет на складе — обратитесь в поддержку)",
            "delivered": "✅ выдан",
            "failed": "❌ ошибка",
            "refunded": "💸 возврат",
        }.get(o["status"], o["status"])
        key_line = f"\n  └ <code>{escape(o['key'])}</code>" if o["key"] else ""
        parts.append(f"#{o['id']} • {date}\n  {escape(o['product'])}\n  {status}{key_line}")
    await m.answer("\n\n".join(parts))


@router.message(F.text == "🎁 Промокод")
async def on_promo_msg(m: Message):
    await m.answer(
        "Промокод применяется при покупке в магазине: открой товар → введи код → купи со скидкой."
    )


@router.message(F.text == "🆘 Поддержка")
async def on_support(m: Message):
    await m.answer("По вопросам пишите администратору магазина.")


# ---------- Stars payment ----------
@router.pre_checkout_query()
async def on_pre_checkout(q: PreCheckoutQuery, bot: Bot, conn):
    payload = q.invoice_payload  # "order:<id>"
    ok = False
    if payload.startswith("order:"):
        try:
            order_id = int(payload.split(":", 1)[1])
        except ValueError:
            order_id = 0
        if order_id:
            o = await db.get_order(conn, order_id)
            if o and o["status"] == "pending" and o["currency"] == "XTR" and o["amount"] == q.total_amount:
                ok = True
    await bot.answer_pre_checkout_query(q.id, ok=ok,
                                        error_message=None if ok else "Order is invalid or expired.")


@router.message(F.successful_payment)
async def on_paid_stars(m: Message, conn):
    sp = m.successful_payment
    payload = sp.invoice_payload or ""
    if not payload.startswith("order:"):
        return
    try:
        order_id = int(payload.split(":", 1)[1])
    except ValueError:
        return
    await db.set_order_provider_ref(conn, order_id, sp.telegram_payment_charge_id)
    key = await db.fulfill_order(conn, order_id)
    if key:
        await m.answer(
            f"✅ <b>Оплата получена.</b>\nВаш ключ:\n<code>{escape(key)}</code>\n\n"
            f"Не теряй — ключ можно посмотреть в <b>🧾 Мои покупки</b>.",
        )
    else:
        await m.answer(
            "Оплата получена, но ключей временно нет на складе. "
            "Администратор скоро выдаст вручную или вернёт средства."
        )


# ---------- Admin ----------
def is_admin(user_id: int, cfg: Config) -> bool:
    return user_id in cfg.admin_ids


@router.message(Command("admin"))
async def admin_menu(m: Message, cfg: Config):
    if not is_admin(m.from_user.id, cfg):
        return
    await m.answer(
        "<b>Админ:</b>\n"
        "/stats — статистика\n"
        "/addcat &lt;emoji&gt; &lt;name&gt;\n"
        "/addproduct &lt;cat_id&gt;|&lt;name&gt;|&lt;desc&gt;|&lt;duration&gt;|&lt;price_rub&gt;|&lt;price_stars&gt;\n"
        "/addkeys &lt;product_id&gt;\\n(затем построчно ключи)\n"
        "/addpromo &lt;CODE&gt; &lt;pct&gt; &lt;max_uses&gt; &lt;days_ttl|0&gt;\n"
        "/cats — список категорий\n"
        "/products — список товаров"
    )


@router.message(Command("stats"))
async def cmd_stats(m: Message, cfg: Config, conn):
    if not is_admin(m.from_user.id, cfg):
        return
    s = await db.stats(conn)
    usdt = s["usdt_micro_total"] / 1_000_000
    await m.answer(
        f"👥 Юзеров: {s['users']}\n"
        f"✅ Продаж: {s['sales']}\n"
        f"⭐ Stars: {s['stars_total']}\n"
        f"💵 USDT: {usdt:.2f}\n"
        f"🔑 Свободных ключей: {s['free_keys']}"
    )


@router.message(Command("cats"))
async def cmd_cats(m: Message, cfg: Config, conn):
    if not is_admin(m.from_user.id, cfg):
        return
    cats = await db.list_categories(conn)
    if not cats:
        await m.answer("Нет категорий.")
        return
    lines = [f"{c['id']}. {c['emoji']} {escape(c['name'])}" for c in cats]
    await m.answer("\n".join(lines))


@router.message(Command("products"))
async def cmd_products(m: Message, cfg: Config, conn):
    if not is_admin(m.from_user.id, cfg):
        return
    products = await db.list_products(conn)
    if not products:
        await m.answer("Нет товаров.")
        return
    lines = [
        f"{p['id']}. {escape(p['name'])} — {p['price_rub']/100:.0f}₽ / {p['price_stars']}⭐ • остаток: {p['stock']}"
        for p in products
    ]
    await m.answer("\n".join(lines))


@router.message(Command("addcat"))
async def cmd_addcat(m: Message, cfg: Config, conn):
    if not is_admin(m.from_user.id, cfg):
        return
    parts = (m.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await m.answer("Формат: /addcat 🎯 Standoff 2")
        return
    cid = await db.add_category(conn, parts[2].strip(), parts[1])
    await m.answer(f"OK, категория #{cid}")


@router.message(Command("addproduct"))
async def cmd_addproduct(m: Message, cfg: Config, conn):
    if not is_admin(m.from_user.id, cfg):
        return
    raw = (m.text or "").split(maxsplit=1)
    if len(raw) < 2:
        await m.answer("Формат: /addproduct cat_id|name|desc|duration|price_rub|price_stars")
        return
    try:
        cat, name, desc, dur, prub, pstars = [x.strip() for x in raw[1].split("|")]
        pid = await db.add_product(conn, int(cat), name, desc, dur, int(float(prub) * 100), int(pstars))
    except Exception as e:
        await m.answer(f"Ошибка: {e}")
        return
    await m.answer(f"OK, товар #{pid}")


@router.message(Command("addkeys"))
async def cmd_addkeys(m: Message, cfg: Config, conn):
    if not is_admin(m.from_user.id, cfg):
        return
    lines = (m.text or "").splitlines()
    if len(lines) < 2:
        await m.answer("Первой строкой: /addkeys <product_id>\nДалее построчно ключи.")
        return
    try:
        pid = int(lines[0].split()[1])
    except (IndexError, ValueError):
        await m.answer("Не понял product_id.")
        return
    keys = [ln.strip() for ln in lines[1:] if ln.strip()]
    if not keys:
        await m.answer("Ключей нет.")
        return
    n = await db.add_keys(conn, pid, keys)
    await m.answer(f"Загружено ключей: {n}")


@router.message(Command("addpromo"))
async def cmd_addpromo(m: Message, cfg: Config, conn):
    if not is_admin(m.from_user.id, cfg):
        return
    parts = (m.text or "").split()
    if len(parts) != 5:
        await m.answer("Формат: /addpromo CODE 15 100 7  (15% / 100 исп. / 7 дней; 0 = бессрочно)")
        return
    try:
        _, code, pct, mx, ttl = parts
        pct = int(pct); mx = int(mx); ttl = int(ttl)
        expires = int(time.time()) + ttl * 86400 if ttl > 0 else None
        await db.add_promo(conn, code, pct, mx, expires)
    except Exception as e:
        await m.answer(f"Ошибка: {e}")
        return
    await m.answer("Промокод сохранён.")


# ---------- helpers ----------
def escape(s: str | None) -> str:
    if not s:
        return ""
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


# ---------- Stars invoice sender (called from API) ----------
async def send_stars_invoice(bot: Bot, chat_id: int, order_id: int, title: str,
                             description: str, amount_stars: int) -> int:
    """Send invoice to chat. Returns message_id."""
    msg = await bot.send_invoice(
        chat_id=chat_id,
        title=title[:32],
        description=description[:255] or title[:255],
        payload=f"order:{order_id}",
        provider_token="",            # empty for Stars
        currency="XTR",
        prices=[LabeledPrice(label=title[:32], amount=amount_stars)],
        start_parameter=f"o{order_id}",
    )
    return msg.message_id


async def create_stars_invoice_link(bot: Bot, order_id: int, title: str,
                                    description: str, amount_stars: int) -> str:
    """Create an invoice slug that MiniApp can pass to Telegram.WebApp.openInvoice
    so the user pays without leaving the MiniApp."""
    return await bot.create_invoice_link(
        title=title[:32],
        description=(description or title)[:255],
        payload=f"order:{order_id}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=title[:32], amount=amount_stars)],
    )


def build_dispatcher(cfg: Config, conn) -> Dispatcher:
    dp = Dispatcher()
    dp["cfg"] = cfg
    dp["conn"] = conn
    dp.include_router(router)
    return dp


async def make_bot(cfg: Config) -> Bot:
    return Bot(token=cfg.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


async def run_polling(cfg: Config, conn):
    bot = await make_bot(cfg)
    dp = build_dispatcher(cfg, conn)
    await bot.delete_webhook(drop_pending_updates=True)
    log.info("Bot polling started")
    await dp.start_polling(bot)
