"""SQLite layer. All writes that hand out a resource (key, promo use, order paid)
are guarded by a single transaction so concurrent buyers can't double-spend."""
import aiosqlite
import time
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id      INTEGER PRIMARY KEY,
    username     TEXT,
    first_name   TEXT,
    created_at   INTEGER NOT NULL,
    is_banned    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS categories (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    name   TEXT NOT NULL UNIQUE,
    emoji  TEXT NOT NULL DEFAULT '🎮',
    sort   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS products (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id  INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    duration     TEXT NOT NULL DEFAULT '',  -- '1 день' / '30 дней' / 'lifetime'
    price_rub    INTEGER NOT NULL,          -- minor units: kopecks
    price_stars  INTEGER NOT NULL,          -- XTR units
    active       INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_products_cat ON products(category_id, active);

CREATE TABLE IF NOT EXISTS keys (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    value       TEXT NOT NULL,
    used        INTEGER NOT NULL DEFAULT 0,
    used_by     INTEGER,
    used_at     INTEGER,
    order_id    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_keys_free ON keys(product_id, used);

CREATE TABLE IF NOT EXISTS promocodes (
    code            TEXT PRIMARY KEY,
    discount_pct    INTEGER NOT NULL CHECK(discount_pct BETWEEN 1 AND 100),
    max_uses        INTEGER NOT NULL DEFAULT 0,    -- 0 = unlimited
    used_count      INTEGER NOT NULL DEFAULT 0,
    expires_at      INTEGER,                       -- unix seconds, NULL = never
    active          INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS orders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    product_id      INTEGER NOT NULL,
    promo           TEXT,
    currency        TEXT NOT NULL,   -- 'XTR' | 'USDT'
    amount          INTEGER NOT NULL, -- XTR units OR USDT*1e6 micro units
    status          TEXT NOT NULL DEFAULT 'pending', -- pending|paid|delivered|failed|refunded
    provider        TEXT NOT NULL,   -- 'stars' | 'cryptobot'
    provider_ref    TEXT,            -- stars charge id or cryptobot invoice id
    key_id          INTEGER,
    created_at      INTEGER NOT NULL,
    paid_at         INTEGER
);
CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id);
CREATE INDEX IF NOT EXISTS idx_orders_ref ON orders(provider, provider_ref);
"""


async def init_db(path: str) -> None:
    async with aiosqlite.connect(path) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")
        await db.executescript(SCHEMA)
        # Seed example category if empty
        cur = await db.execute("SELECT COUNT(*) FROM categories")
        row = await cur.fetchone()
        if row and row[0] == 0:
            await db.executemany(
                "INSERT INTO categories(name, emoji, sort) VALUES (?, ?, ?)",
                [("Standoff 2", "🎯", 1), ("PUBG Mobile", "🪖", 2), ("CS2", "🔫", 3)],
            )
        await db.commit()


def connect(path: str):
    return aiosqlite.connect(path)


# ---------- users ----------
async def upsert_user(db: aiosqlite.Connection, user_id: int, username: str | None, first_name: str | None) -> None:
    await db.execute(
        "INSERT INTO users(user_id, username, first_name, created_at) VALUES (?,?,?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name",
        (user_id, username, first_name, int(time.time())),
    )
    await db.commit()


async def is_banned(db: aiosqlite.Connection, user_id: int) -> bool:
    cur = await db.execute("SELECT is_banned FROM users WHERE user_id=?", (user_id,))
    row = await cur.fetchone()
    return bool(row and row[0])


# ---------- catalog ----------
async def list_categories(db: aiosqlite.Connection) -> list[dict]:
    cur = await db.execute("SELECT id, name, emoji FROM categories ORDER BY sort, id")
    return [dict(id=r[0], name=r[1], emoji=r[2]) for r in await cur.fetchall()]


async def list_products(db: aiosqlite.Connection, category_id: int | None = None) -> list[dict]:
    if category_id:
        cur = await db.execute(
            "SELECT p.id, p.name, p.description, p.duration, p.price_rub, p.price_stars, p.category_id, "
            "(SELECT COUNT(*) FROM keys k WHERE k.product_id=p.id AND k.used=0) "
            "FROM products p WHERE p.active=1 AND p.category_id=? ORDER BY p.id",
            (category_id,),
        )
    else:
        cur = await db.execute(
            "SELECT p.id, p.name, p.description, p.duration, p.price_rub, p.price_stars, p.category_id, "
            "(SELECT COUNT(*) FROM keys k WHERE k.product_id=p.id AND k.used=0) "
            "FROM products p WHERE p.active=1 ORDER BY p.id"
        )
    return [
        dict(id=r[0], name=r[1], description=r[2], duration=r[3],
             price_rub=r[4], price_stars=r[5], category_id=r[6], stock=r[7])
        for r in await cur.fetchall()
    ]


async def get_product(db: aiosqlite.Connection, product_id: int) -> Optional[dict]:
    cur = await db.execute(
        "SELECT id, category_id, name, description, duration, price_rub, price_stars, active "
        "FROM products WHERE id=?",
        (product_id,),
    )
    r = await cur.fetchone()
    if not r:
        return None
    return dict(id=r[0], category_id=r[1], name=r[2], description=r[3],
                duration=r[4], price_rub=r[5], price_stars=r[6], active=bool(r[7]))


async def add_product(db, category_id: int, name: str, description: str, duration: str,
                      price_rub: int, price_stars: int) -> int:
    cur = await db.execute(
        "INSERT INTO products(category_id, name, description, duration, price_rub, price_stars) "
        "VALUES (?,?,?,?,?,?)",
        (category_id, name, description, duration, price_rub, price_stars),
    )
    await db.commit()
    return cur.lastrowid


async def add_category(db, name: str, emoji: str) -> int:
    cur = await db.execute("INSERT INTO categories(name, emoji) VALUES (?,?)", (name, emoji))
    await db.commit()
    return cur.lastrowid


# ---------- keys ----------
async def add_keys(db, product_id: int, values: list[str]) -> int:
    await db.executemany(
        "INSERT INTO keys(product_id, value) VALUES (?,?)",
        [(product_id, v) for v in values],
    )
    await db.commit()
    return len(values)


async def stock_for(db, product_id: int) -> int:
    cur = await db.execute("SELECT COUNT(*) FROM keys WHERE product_id=? AND used=0", (product_id,))
    row = await cur.fetchone()
    return row[0] if row else 0


# ---------- promocodes ----------
async def add_promo(db, code: str, discount_pct: int, max_uses: int, expires_at: int | None) -> None:
    await db.execute(
        "INSERT INTO promocodes(code, discount_pct, max_uses, expires_at) VALUES (?,?,?,?) "
        "ON CONFLICT(code) DO UPDATE SET discount_pct=excluded.discount_pct, "
        "max_uses=excluded.max_uses, expires_at=excluded.expires_at, active=1",
        (code.upper(), discount_pct, max_uses, expires_at),
    )
    await db.commit()


async def get_valid_promo(db, code: str) -> Optional[dict]:
    cur = await db.execute(
        "SELECT code, discount_pct, max_uses, used_count, expires_at, active FROM promocodes WHERE code=?",
        (code.upper(),),
    )
    r = await cur.fetchone()
    if not r:
        return None
    if not r[5]:
        return None
    if r[4] is not None and r[4] < int(time.time()):
        return None
    if r[2] > 0 and r[3] >= r[2]:
        return None
    return dict(code=r[0], discount_pct=r[1], max_uses=r[2], used_count=r[3], expires_at=r[4])


# ---------- orders ----------
async def create_order(db, user_id: int, product_id: int, promo: str | None,
                       currency: str, amount: int, provider: str) -> int:
    cur = await db.execute(
        "INSERT INTO orders(user_id, product_id, promo, currency, amount, provider, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (user_id, product_id, promo, currency, amount, provider, int(time.time())),
    )
    await db.commit()
    return cur.lastrowid


async def set_order_provider_ref(db, order_id: int, provider_ref: str) -> None:
    await db.execute("UPDATE orders SET provider_ref=? WHERE id=?", (provider_ref, order_id))
    await db.commit()


async def get_order(db, order_id: int) -> Optional[dict]:
    cur = await db.execute(
        "SELECT id,user_id,product_id,promo,currency,amount,status,provider,provider_ref,key_id,created_at,paid_at "
        "FROM orders WHERE id=?",
        (order_id,),
    )
    r = await cur.fetchone()
    if not r:
        return None
    return dict(id=r[0], user_id=r[1], product_id=r[2], promo=r[3], currency=r[4],
                amount=r[5], status=r[6], provider=r[7], provider_ref=r[8],
                key_id=r[9], created_at=r[10], paid_at=r[11])


async def find_order_by_ref(db, provider: str, provider_ref: str) -> Optional[dict]:
    cur = await db.execute(
        "SELECT id,user_id,product_id,promo,currency,amount,status FROM orders "
        "WHERE provider=? AND provider_ref=?",
        (provider, provider_ref),
    )
    r = await cur.fetchone()
    if not r:
        return None
    return dict(id=r[0], user_id=r[1], product_id=r[2], promo=r[3], currency=r[4],
                amount=r[5], status=r[6])


async def my_orders(db, user_id: int) -> list[dict]:
    cur = await db.execute(
        "SELECT o.id, p.name, o.status, o.currency, o.amount, o.created_at, k.value "
        "FROM orders o JOIN products p ON p.id=o.product_id "
        "LEFT JOIN keys k ON k.id=o.key_id "
        "WHERE o.user_id=? ORDER BY o.id DESC LIMIT 50",
        (user_id,),
    )
    rows = await cur.fetchall()
    return [
        dict(id=r[0], product=r[1], status=r[2], currency=r[3], amount=r[4],
             created_at=r[5], key=r[6])
        for r in rows
    ]


# ---------- the critical atomic operation ----------
async def fulfill_order(db, order_id: int) -> Optional[str]:
    """Mark order as paid, atomically claim a free key, mark order delivered.
    Returns the key value, or None if no stock OR order already fulfilled.
    Idempotent: a second call returns the previously delivered key."""
    await db.execute("BEGIN IMMEDIATE")
    try:
        cur = await db.execute(
            "SELECT status, product_id, key_id, user_id, promo FROM orders WHERE id=?",
            (order_id,),
        )
        order = await cur.fetchone()
        if not order:
            await db.execute("ROLLBACK")
            return None
        status, product_id, key_id, user_id, promo = order

        if status == "delivered" and key_id:
            cur = await db.execute("SELECT value FROM keys WHERE id=?", (key_id,))
            r = await cur.fetchone()
            await db.execute("ROLLBACK")
            return r[0] if r else None

        # Claim one free key (lowest id wins under SQLite serializable IMMEDIATE tx)
        cur = await db.execute(
            "SELECT id FROM keys WHERE product_id=? AND used=0 ORDER BY id LIMIT 1",
            (product_id,),
        )
        free = await cur.fetchone()
        if not free:
            # No stock -> mark paid but undelivered; admin will refund or refill
            await db.execute(
                "UPDATE orders SET status='paid', paid_at=? WHERE id=?",
                (int(time.time()), order_id),
            )
            await db.commit()
            return None

        kid = free[0]
        await db.execute(
            "UPDATE keys SET used=1, used_by=?, used_at=?, order_id=? WHERE id=? AND used=0",
            (user_id, int(time.time()), order_id, kid),
        )
        await db.execute(
            "UPDATE orders SET status='delivered', paid_at=?, key_id=? WHERE id=?",
            (int(time.time()), kid, order_id),
        )
        if promo:
            await db.execute(
                "UPDATE promocodes SET used_count=used_count+1 WHERE code=?", (promo,),
            )
        cur = await db.execute("SELECT value FROM keys WHERE id=?", (kid,))
        r = await cur.fetchone()
        await db.commit()
        return r[0] if r else None
    except Exception:
        await db.execute("ROLLBACK")
        raise


# ---------- stats ----------
async def stats(db) -> dict:
    out: dict = {}
    for q, k in [
        ("SELECT COUNT(*) FROM users", "users"),
        ("SELECT COUNT(*) FROM orders WHERE status='delivered'", "sales"),
        ("SELECT COALESCE(SUM(amount),0) FROM orders WHERE status='delivered' AND currency='XTR'", "stars_total"),
        ("SELECT COALESCE(SUM(amount),0) FROM orders WHERE status='delivered' AND currency='USDT'", "usdt_micro_total"),
        ("SELECT COUNT(*) FROM keys WHERE used=0", "free_keys"),
    ]:
        cur = await db.execute(q)
        r = await cur.fetchone()
        out[k] = r[0] if r else 0
    return out
