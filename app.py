#!/usr/bin/env python3
"""Small, dependency-free storefront + admin API backed by SQLite."""
from __future__ import annotations

import base64
import csv
import gzip
import hashlib
import hmac
import io
import json
import mimetypes
import os
import re
import secrets
import signal
import sqlite3
import sys
import threading
import time
import traceback
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.utils import formatdate
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))  # sibling modules also when started with `python3 -I app.py`
import legal  # noqa: E402
import seo  # noqa: E402
import totp  # noqa: E402

VERSION = "2.0.0"


def load_local_env() -> None:
    """Read KEY=VALUE lines from a git-ignored .env beside app.py; variables already in the environment win."""
    name = os.environ.get("ENV_FILE", "")
    if name.lower() == "off":
        return
    try:
        lines = (Path(name).expanduser() if name else BASE_DIR / ".env").read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


load_local_env()
# DATA_DIR holds the mutable state (store.db + uploads/). Point it at a persistent volume in production.
DATA_DIR = Path(os.environ.get("DATA_DIR") or BASE_DIR).expanduser().resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "store.db"
UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
ASSETS_DIR = BASE_DIR / "assets"
ICONS_DIR = BASE_DIR / "icons"
# Loopback by default; set HOST=0.0.0.0 only for LAN testing or inside a container behind a reverse proxy.
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "4173"))
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
# No default password lives in the source: it comes from ADMIN_PASSWORD or is generated at first run.
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
# Set TRUST_PROXY=1 only when running behind a reverse proxy that sets X-Forwarded-For / X-Forwarded-Proto.
TRUST_PROXY = os.environ.get("TRUST_PROXY", "") == "1"
# Optional canonical origin (https://example.com) used for robots.txt / sitemap.xml.
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
# Design phase on one machine only: ALLOW_WEAK_PASSWORD=1 lets the first admin password be as short as 6 characters.
# It is refused outright unless the server listens on loopback with no proxy and no public address.
LOCAL_ONLY = HOST in ("127.0.0.1", "localhost", "::1") and not TRUST_PROXY and not PUBLIC_BASE_URL
ALLOW_WEAK_PASSWORD = os.environ.get("ALLOW_WEAK_PASSWORD", "") == "1"
if ALLOW_WEAK_PASSWORD and not LOCAL_ONLY:
    raise SystemExit("ALLOW_WEAK_PASSWORD=1 مسموح فقط عند التشغيل المحلي (HOST=127.0.0.1 بدون TRUST_PROXY وبدون PUBLIC_BASE_URL). احذفه قبل النشر.")
MIN_PASSWORD = 6 if ALLOW_WEAK_PASSWORD else 12
SESSION_TTL = 12 * 60 * 60
MAX_BODY = 512 * 1024                      # default for JSON bodies (orders, settings, login ...)
MAX_UPLOAD_BODY = 9 * 1024 * 1024         # one base64-encoded image (<= 5 MB) + its thumbnail
MAX_IMAGE_BYTES = 5 * 1024 * 1024
ORDER_LIMIT = (int(os.environ.get("ORDER_LIMIT_PER_10MIN", "10")), 600)  # public order attempts per client per 10 minutes
IMAGE_PATH_RE = re.compile(r"/?(?:assets|uploads)/[A-Za-z0-9][A-Za-z0-9._-]*")
IDEMPOTENT_ORDERS: dict[str, tuple[float, dict]] = {}
HOST_RE = re.compile(r"[A-Za-z0-9.\-]+(?::\d{1,5})?|\[[0-9A-Fa-f:]+\](?::\d{1,5})?")
# Root-level files the server may serve (everything else is 404). Values are paths under BASE_DIR.
PUBLIC_FILES = {
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/admin-manifest.webmanifest": ("admin-manifest.webmanifest", "application/manifest+json"),
    "/sw.js": ("sw.js", "text/javascript"),
    "/favicon.ico": ("icons/favicon.ico", "image/x-icon"),
}
DEFAULT_SHARE_IMAGE = "/assets/wavy-11.jpg"
COMPRESSIBLE = ("text/", "application/json", "application/javascript", "application/manifest+json", "application/xml", "image/svg+xml")
LOG_JSON = os.environ.get("LOG_FORMAT", "") == "json"
IDEMPOTENCY_TTL = 600


def build_csp(nonce: str, secure: bool) -> str:
    """No 'unsafe-inline' for scripts or <style> blocks: only our own files and tags carrying this response's nonce.
    Style *attributes* (style="...") stay allowed; they cannot run code."""
    parts = ["default-src 'self'", "img-src 'self' data:", f"style-src 'self' 'nonce-{nonce}'", "style-src-attr 'unsafe-inline'",
             f"script-src 'self' 'nonce-{nonce}'", "connect-src 'self'", "manifest-src 'self'", "worker-src 'self'",
             "object-src 'none'", "base-uri 'self'", "form-action 'self'", "frame-ancestors 'none'"]
    if secure:
        parts.append("upgrade-insecure-requests")
    return "; ".join(parts)

CATEGORIES = [
    ("curtains", "ستائر تفصيل", 1),
    ("roller", "ستائر رول", 2),
    ("blinds", "ستائر شرائح معدنية", 3),
    ("electric", "ستائر كهربائية", 4),
    ("fabrics", "أقمشة الستائر", 5),
]
# Sample rows that earlier versions seeded (slug -> name, price). They are placeholders, not store data: a database
# that still holds them UNMODIFIED gets them retired once at startup (deleted, or hidden if an order references them).
LEGACY_SAMPLES = {
    "sample-1": ("ستائر بلاك أوت تفصيل", 450), "sample-2": ("ستائر شيفون ناعمة", 350),
    "sample-3": ("ستارة رول بلاك أوت", 280), "sample-4": ("ستائر شرائح معدنية", 320),
    "sample-5": ("ستائر كهربائية بالتحكم", 1450), "sample-6": ("أقمشة ستائر — السعر للمتر", 95),
    "sample-7": ("ستارة مزدوجة شيفون وبلاك أوت", 680), "sample-8": ("ستارة رول نهاري وليلي", 430),
}
# The owner's real "wavy curtains, tailored to order" photos (assets/wavy-NN.jpg). The shop's catalogue shows no
# prices for them, so price is 0, which the storefront and admin display as "price on request" (set after measuring).
WAVY_DESCRIPTION = ("ستائر ويفي بطيّات متموجة تُفصَّل حسب الطلب. يتحدد السعر النهائي بحسب المقاس والخامة واللون؛ "
                    "تواصل مع المتجر لمعاينة الخيارات وتأكيد التكلفة.")
WAVY_ALTS = [
    "ستائر ويفي رمادية مع شيفون لنافذة بانورامية بثلاثة أقسام",
    "ستائر ويفي رمادية مع شيفون أبيض لنافذة كبيرة",
    "ستائر ويفي كريمية ورمادية مع شيفون في صالة بإضاءة مخفية وثريا",
    "ستائر ويفي كريمية على نافذة طويلة بإطار أسود",
    "ستائر ويفي رمادية بيج مع شيفون أبيض في مجلس",
    "ستائر ويفي بنية مع شيفون في غرفة جلوس",
    "ستائر ويفي رمادية فاتحة تغطي جدارًا كاملًا",
    "ستائر ويفي كريمية مع إضاءة مخفية في غرفة جلوس",
    "ستائر ويفي رمادية داكنة مع شيفون في غرفة نوم",
    "ستائر ويفي بنية مع شيفون في غرفة طعام",
    "ستائر ويفي كريمية مع شيفون لنافذة عريضة",
    "ستائر ويفي بيج مع ربطات جانبية لنافذة صغيرة",
    "ستائر ويفي كريمية مع شيفون لنافذة مقوّسة",
    "ستائر ويفي رمادية وبيج مع شيفون لنافذة مقوّسة",
    "ستائر ويفي رمادية مع شيفون على جدار طويل بإضاءة مخفية",
    "ستائر ويفي رمادية مع شيفون أبيض ولمبة سقف",
    "ستائر ويفي رمادية فاتحة مع شيفون لنافذة",
    "ستائر ويفي بيج مع شيفون لنافذة بثلاثة أقسام",
]
DEFAULT_SETTINGS = {
    "store_name": "الفخامة للأقمشة والستائر",
    "tagline": "تفصيل ستائر وأقمشة في جدة",
    "city": "جدة",
    "phone": "+966 57 648 6491",
    "whatsapp": "",
    "address": "شارع المكرونة، مجمع الشرق، حي مشرفة، جدة 23444",
    "map_url": "https://maps.app.goo.gl/kn2kxuPujQT9raLG7?g_st=awb",
    "delivery_fee": "0",
    "delivery_note": "تفصيل الستائر حسب المقاس؛ تواصل مع المتجر لتأكيد التوصيل والتركيب بحسب موقعك.",
    "instagram": "",
    "site_ready": "0",
    "seo_title": "الفخامة للأقمشة والستائر | تفصيل ستائر في جدة",
    "seo_description": "الفخامة للأقمشة والستائر في جدة: تفصيل ستائر، ستائر رول وشرائح معدنية وكهربائية، وأقمشة متنوعة. زورونا في شارع المكرونة، مجمع الشرق، حي مشرفة.",
    "hero_title": "ستائر تكمّل أناقة بيتك.",
    "hero_copy": "من الأقمشة إلى الستائر المفصّلة، اختَر ما يناسب نافذتك وذوقك. تفصيل ستائر، رول، شرائح معدنية وكهربائية في جدة.",
}
ORDER_STATUSES = {
    "new": "جديد",
    "confirmed": "تم التأكيد",
    "preparing": "قيد التجهيز",
    "shipped": "تم الشحن",
    "delivered": "تم التسليم",
    "cancelled": "ملغي",
}


class APIError(Exception):
    def __init__(self, message: str, status: int = 400, headers: dict | None = None, extra: dict | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.headers = headers or {}
        self.extra = extra or {}


@contextmanager
def connect_db():
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 20000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def audit(conn: sqlite3.Connection, actor: str, action: str, detail: str = "", ip: str = "") -> None:
    """Accountability trail of admin activity (who/what/when/from where). Never records passwords, codes or tokens."""
    conn.execute("INSERT INTO audit_log(at,actor,action,detail,ip) VALUES(?,?,?,?,?)", (now_iso(), actor[:100], action[:60], detail[:300], ip[:64]))
    conn.execute("DELETE FROM audit_log WHERE id <= (SELECT MAX(id) FROM audit_log) - 5000")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 310_000)
    return f"{salt.hex()}${derived.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        salt_hex, expected = encoded.split("$", 1)
        actual = hash_password(password, bytes.fromhex(salt_hex)).split("$", 1)[1]
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def init_db() -> str | None:
    """Create the schema and seed data. Returns the generated admin password on first run, else None."""
    generated_password: str | None = None
    with connect_db() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript("""
          CREATE TABLE IF NOT EXISTS categories (
            slug TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1
          );
          CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            slug TEXT NOT NULL UNIQUE,
            category TEXT NOT NULL REFERENCES categories(slug) ON UPDATE CASCADE ON DELETE RESTRICT,
            description TEXT NOT NULL DEFAULT '',
            price INTEGER NOT NULL DEFAULT 0,
            image TEXT NOT NULL DEFAULT '',
            alt TEXT NOT NULL DEFAULT '',
            badge TEXT NOT NULL DEFAULT '',
            sku TEXT NOT NULL DEFAULT '',
            stock INTEGER NOT NULL DEFAULT 0,
            featured INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
          );
          CREATE INDEX IF NOT EXISTS idx_products_active ON products(active, category, id);
          CREATE TABLE IF NOT EXISTS product_images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            path TEXT NOT NULL,
            thumb TEXT NOT NULL DEFAULT '',
            position INTEGER NOT NULL DEFAULT 0
          );
          CREATE INDEX IF NOT EXISTS idx_product_images ON product_images(product_id, position);
          CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            at TEXT NOT NULL,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            detail TEXT NOT NULL DEFAULT '',
            ip TEXT NOT NULL DEFAULT ''
          );
          CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT ''
          );
          CREATE TABLE IF NOT EXISTS admins (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            updated_at TEXT NOT NULL
          );
          CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_number TEXT NOT NULL UNIQUE,
            customer_name TEXT NOT NULL,
            phone TEXT NOT NULL,
            city TEXT NOT NULL,
            address TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'new',
            subtotal INTEGER NOT NULL DEFAULT 0,
            delivery_fee INTEGER NOT NULL DEFAULT 0,
            total INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
          );
          CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_at DESC);
          CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
          CREATE TABLE IF NOT EXISTS order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
            product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
            product_name TEXT NOT NULL,
            sku TEXT NOT NULL DEFAULT '',
            unit_price INTEGER NOT NULL,
            quantity INTEGER NOT NULL
          );
        """)
        for table, column, ddl in (("categories", "image", "TEXT NOT NULL DEFAULT ''"),
                                   ("orders", "consent_at", "TEXT NOT NULL DEFAULT ''"),
                                   ("admins", "totp_secret", "TEXT NOT NULL DEFAULT ''")):
            if column not in {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        for slug, name, order in CATEGORIES:
            conn.execute("INSERT OR IGNORE INTO categories(slug,name,sort_order,active) VALUES(?,?,?,1)", (slug, name, order))
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (key, value))
        if not conn.execute("SELECT 1 FROM settings WHERE key='sample_cleanup_v1'").fetchone():
            for slug, (name, price) in LEGACY_SAMPLES.items():
                row = conn.execute("SELECT id FROM products WHERE slug=? AND name=? AND price=? AND (image LIKE '/assets/%' OR image LIKE 'assets/%')", (slug, name, price)).fetchone()
                if not row:
                    continue
                if conn.execute("SELECT 1 FROM order_items WHERE product_id=? LIMIT 1", (row["id"],)).fetchone():
                    conn.execute("UPDATE products SET active=0 WHERE id=?", (row["id"],))  # keep order history intact
                else:
                    conn.execute("DELETE FROM products WHERE id=?", (row["id"],))
            conn.execute("INSERT INTO settings(key,value) VALUES('sample_cleanup_v1','1')")
        seeded = conn.execute("SELECT value FROM settings WHERE key='seed_wavy_v1'").fetchone()
        if not seeded:
            ts = now_iso()
            # Highest id first in the storefront (featured DESC, id DESC): insert model 18 first so model 1 shows first.
            for number in range(len(WAVY_ALTS), 0, -1):
                conn.execute("""INSERT OR IGNORE INTO products(name,slug,category,description,price,image,alt,badge,sku,stock,featured,active,created_at,updated_at)
                              VALUES(?,?,?,?,0,?,?,?,?,999,1,1,?,?)""",
                             (f"ستائر ويفي تفصيل حسب الطلب — موديل {number}", f"wavy-{number:02d}", "curtains", WAVY_DESCRIPTION,
                              f"/assets/wavy-{number:02d}.jpg", WAVY_ALTS[number - 1], "تفصيل حسب الطلب", f"FAL-WAV-{number:03d}", ts, ts))
            conn.execute("INSERT INTO settings(key,value) VALUES('seed_wavy_v1','1')")
        conn.execute("INSERT INTO product_images(product_id,path,thumb,position) SELECT id,image,'',0 FROM products "
                     "WHERE image!='' AND id NOT IN (SELECT product_id FROM product_images)")
        for row in conn.execute("SELECT id,path FROM product_images WHERE thumb='' AND path LIKE '/assets/wavy-%'").fetchall():
            thumb = thumb_for(row["path"])
            if thumb:
                conn.execute("UPDATE product_images SET thumb=? WHERE id=?", (thumb, row["id"]))
        # Earlier versions stored relative image paths ("assets/x.jpg") that break under /admin/.
        conn.execute("UPDATE products SET image='/'||image WHERE image LIKE 'assets/%' OR image LIKE 'uploads/%'")
        admin = conn.execute("SELECT username FROM admins LIMIT 1").fetchone()
        if not admin:
            username = ADMIN_USERNAME.strip() or "admin"
            password = ADMIN_PASSWORD
            if not password:
                password = generated_password = secrets.token_urlsafe(15)
            elif len(password) < MIN_PASSWORD:
                raise RuntimeError(f"ADMIN_PASSWORD must be at least {MIN_PASSWORD} characters")
            conn.execute("INSERT INTO admins(username,password_hash,updated_at) VALUES(?,?,?)",
                         (username, hash_password(password), now_iso()))
    return generated_password


def get_settings(conn: sqlite3.Connection) -> dict:
    data = DEFAULT_SETTINGS.copy()
    data.update({row["key"]: row["value"] for row in conn.execute("SELECT key,value FROM settings")})
    return data


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).strip().lower()
    value = re.sub(r"[^\w\-]+", "-", value, flags=re.UNICODE).strip("-")
    return value[:110] or f"product-{secrets.token_hex(4)}"


def int_field(value, label: str, minimum: int = 0, maximum: int = 100_000_000) -> int:
    try:
        number = int(value)
    except (ValueError, TypeError):
        raise APIError(f"قيمة {label} غير صحيحة.")
    if number < minimum or number > maximum:
        raise APIError(f"قيمة {label} خارج النطاق المسموح.")
    return number


def save_image_data(data_uri: str) -> str:
    if not isinstance(data_uri, str) or "," not in data_uri:
        raise APIError("تعذر قراءة الصورة.")
    header, encoded = data_uri.split(",", 1)
    match = re.fullmatch(r"data:image/(jpeg|jpg|png|webp);base64", header, re.I)
    if not match:
        raise APIError("اختر صورة بصيغة JPG أو PNG أو WebP.")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, base64.binascii.Error):
        raise APIError("ملف الصورة غير صالح.")
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise APIError("حجم الصورة يجب ألا يتجاوز 5 ميغابايت.", 413)
    ext = match.group(1).lower()
    if ext in ("jpeg", "jpg"):
        valid = raw.startswith(b"\xff\xd8\xff")
        ext = "jpg"
    elif ext == "png":
        valid = raw.startswith(b"\x89PNG\r\n\x1a\n")
    else:
        valid = len(raw) > 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP"
    if not valid:
        raise APIError("محتوى الملف لا يطابق نوع الصورة.")
    filename = f"{uuid.uuid4().hex}.{ext}"
    (UPLOAD_DIR / filename).write_bytes(raw)
    return f"/uploads/{filename}"


def thumb_for(path: str) -> str:
    """Bundled photos have a 600px thumbnail next to them: /assets/wavy-01.jpg -> /assets/wavy-01-t.jpg."""
    match = re.fullmatch(r"/assets/([A-Za-z0-9._-]+)\.jpg", path or "")
    if match and (ASSETS_DIR / f"{match.group(1)}-t.jpg").is_file():
        return f"/assets/{match.group(1)}-t.jpg"
    return ""


def image_file_exists(url: str) -> bool:
    """True for an existing file under assets/ or uploads/ (the only places product images may live)."""
    if not IMAGE_PATH_RE.fullmatch(url or ""):
        return False
    url = "/" + url.lstrip("/")
    root = ASSETS_DIR if url.startswith("/assets/") else UPLOAD_DIR
    return (root / url.split("/", 2)[2]).is_file()


def images_for(conn: sqlite3.Connection, product_ids: list[int]) -> dict[int, list[dict]]:
    """Gallery of every product in one query: {product_id: [{"url","thumb"}, ...]} in display order."""
    result: dict[int, list[dict]] = {pid: [] for pid in product_ids}
    if not product_ids:
        return result
    marks = ",".join("?" * len(product_ids))
    for row in conn.execute(f"SELECT product_id,path,thumb FROM product_images WHERE product_id IN ({marks}) ORDER BY product_id,position,id", product_ids):
        result[row["product_id"]].append({"url": public_image(row["path"]), "thumb": public_image(row["thumb"]) or public_image(row["path"])})
    return result


MAX_GALLERY = 12


def drop_unreferenced_uploads(conn: sqlite3.Connection, paths) -> None:
    """Delete uploaded files (never bundled assets) that no product or category uses any more."""
    for path in {p for p in paths if p and p.startswith("/uploads/")}:
        used = conn.execute("SELECT 1 FROM product_images WHERE path=? OR thumb=? UNION SELECT 1 FROM products WHERE image=? "
                            "UNION SELECT 1 FROM categories WHERE image=? LIMIT 1", (path, path, path, path)).fetchone()
        if not used and IMAGE_PATH_RE.fullmatch(path.lstrip("/")):
            try:
                (UPLOAD_DIR / path.split("/", 2)[2]).unlink()
            except OSError:
                pass


def public_image(value: str) -> str:
    """Images are always served from the site root, whichever page (/ or /admin/) requests them."""
    value = value or ""
    if value and not value.startswith(("/", "http://", "https://", "data:")):
        value = "/" + value
    return value


def product_dict(row: sqlite3.Row, category_name: str | None = None, images: list[dict] | None = None) -> dict:
    gallery = images or ([{"url": public_image(row["image"]), "thumb": public_image(row["image"])}] if row["image"] else [])
    return {
        "images": gallery, "thumb": gallery[0]["thumb"] if gallery else "",
        "id": row["id"], "name": row["name"], "slug": row["slug"], "category": row["category"],
        "category_name": category_name or "", "description": row["description"], "price": row["price"],
        "image": public_image(row["image"]), "alt": row["alt"], "badge": row["badge"], "sku": row["sku"],
        "stock": row["stock"], "featured": bool(row["featured"]), "active": bool(row["active"]),
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }


def order_dict(conn: sqlite3.Connection, row: sqlite3.Row, include_items: bool = True) -> dict:
    result = {key: row[key] for key in row.keys()}
    result["status_label"] = ORDER_STATUSES.get(row["status"], row["status"])
    # True when some lines are "price on request" (unit price 0): the stored total does not include them yet.
    result["on_request"] = bool(conn.execute("SELECT 1 FROM order_items WHERE order_id=? AND unit_price=0 LIMIT 1", (row["id"],)).fetchone())
    if include_items:
        result["items"] = [dict(item) for item in conn.execute(
            "SELECT product_id,product_name,sku,unit_price,quantity FROM order_items WHERE order_id=? ORDER BY id", (row["id"],)
        )]
    return result


SESSIONS: dict[str, dict] = {}
LOGIN_FAILURES: dict[str, list[float]] = {}
ORDER_ATTEMPTS: dict[str, list[float]] = {}


def purge_expired(table: dict, horizon: float, key_expires: bool = False) -> None:
    """Drop stale entries so the in-memory tables cannot grow without bound."""
    now = time.time()
    for key in list(table):
        value = table[key]
        stale = value["expires"] < now if key_expires else not [t for t in value if now - t < horizon]
        if stale:
            table.pop(key, None)


def allow_attempt(table: dict[str, list[float]], key: str, limit: int, window: int) -> bool:
    """Sliding-window limiter. Records the attempt and returns False when `key` is over `limit`."""
    now = time.time()
    recent = [t for t in table.get(key, []) if now - t < window]
    if len(recent) >= limit:
        table[key] = recent
        return False
    recent.append(now)
    table[key] = recent
    if len(table) > 5000:
        purge_expired(table, window)
    return True


class StoreHandler(BaseHTTPRequestHandler):
    server_version = "FakhamahStore"
    sys_version = ""
    request_id = "-"
    started = 0.0
    status_sent = 0
    audited = False
    audit_detail = ""

    def log_request(self, code="-", size="-") -> None:
        if not LOG_JSON:
            return super().log_request(code, size)
        sys.stderr.write(json.dumps({"ts": now_iso(), "ip": self.client_ip(), "method": self.command, "path": self.path.split("?")[0],
                                     "status": code, "ms": round((time.time() - self.started) * 1000), "rid": self.request_id},
                                    ensure_ascii=False) + "\n")

    def log_message(self, fmt: str, *args) -> None:
        # Keep access logs useful without dumping request bodies or credentials.
        if LOG_JSON:
            sys.stderr.write(json.dumps({"ts": now_iso(), "msg": fmt % args}, ensure_ascii=False) + "\n")
        else:
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def client_ip(self) -> str:
        if TRUST_PROXY:
            # The right-most entry is the one appended by our own proxy; earlier ones can be forged.
            forwarded = self.headers.get("X-Forwarded-For", "").split(",")[-1].strip()
            if forwarded:
                return forwarded[:64]
        return self.client_address[0]

    def is_secure(self) -> bool:
        return TRUST_PROXY and self.headers.get("X-Forwarded-Proto", "").split(",")[0].strip().lower() == "https"

    def base_url(self) -> str:
        if PUBLIC_BASE_URL:
            return PUBLIC_BASE_URL
        host = self.headers.get("Host", "")
        if not HOST_RE.fullmatch(host):
            host = f"localhost:{PORT}"
        return f"{'https' if self.is_secure() else 'http'}://{host}"

    def _send(self, status: int, body: bytes, content_type: str = "application/json; charset=utf-8", headers: dict | None = None, nonce: str | None = None) -> None:
        path = urlsplit(self.path).path
        out = {
            "Content-Type": content_type,
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "strict-origin-when-cross-origin",
            "Permissions-Policy": "geolocation=(), microphone=(), payment=(), usb=()",
            "Cross-Origin-Opener-Policy": "same-origin",
            "Cross-Origin-Resource-Policy": "cross-origin" if path.startswith(("/assets/", "/uploads/", "/icons/")) else "same-site",
            "Cache-Control": "no-store",
            "X-Request-ID": self.request_id,
            "Connection": "close",
        }
        if content_type.startswith("text/html") and nonce:
            out["Content-Security-Policy"] = build_csp(nonce, self.is_secure())
        if path.startswith(("/admin", "/api/admin")):
            out["X-Robots-Tag"] = "noindex, nofollow"
        if self.is_secure():
            out["Strict-Transport-Security"] = "max-age=15552000"
        out.update(headers or {})  # callers may override, e.g. Cache-Control for static files
        mime = content_type.split(";")[0].strip()
        compressible = mime.startswith(COMPRESSIBLE)
        if compressible:
            out["Vary"] = "Accept-Encoding"
        etag = out.get("ETag")
        # Conditional GET: unchanged resources cost a few bytes instead of the whole body.
        if status == 200 and etag and self.command in ("GET", "HEAD"):
            client = [t.strip() for t in self.headers.get("If-None-Match", "").split(",")]
            if etag in client or etag.removeprefix("W/") in [c.removeprefix("W/") for c in client]:
                status, body = 304, b""
        if status == 200 and compressible and len(body) >= 800 and "gzip" in self.headers.get("Accept-Encoding", "") and "Content-Encoding" not in out:
            body = gzip.compress(body, 6, mtime=0)
            out["Content-Encoding"] = "gzip"
            if etag and not etag.startswith("W/"):
                out["ETag"] = "W/" + etag
        out["Content-Length"] = str(len(body))
        self.status_sent = status
        self.audit_admin_change()  # written before the response leaves, so the client can read its own action right away
        self.send_response(status)
        for key, value in out.items():
            for item in (value if isinstance(value, list) else [value]):
                self.send_header(key, item)
        self.end_headers()
        if self.command != "HEAD" and status != 304:
            self.wfile.write(body)

    def send_json(self, data, status: int = 200, headers: dict | None = None, revalidate: bool = False) -> None:
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        extra = dict(headers or {})
        if revalidate:  # public catalogue data: always revalidated, but cheap thanks to the ETag
            extra.setdefault("ETag", 'W/"' + hashlib.sha1(body).hexdigest()[:24] + '"')
            extra.setdefault("Cache-Control", "no-cache")
        self._send(status, body, "application/json; charset=utf-8", extra)

    def render_html(self, markup: str, status: int = 200, headers: dict | None = None) -> None:
        """Send an HTML page whose <script>/<style> tags carry a fresh CSP nonce (no 'unsafe-inline')."""
        nonce = secrets.token_urlsafe(16)
        markup = re.sub(r"<(script|style)(?![^>]*\bnonce=)", lambda m: f'<{m.group(1)} nonce="{nonce}"', markup)
        self._send(status, markup.encode("utf-8"), "text/html; charset=utf-8", {"Cache-Control": "no-cache", **(headers or {})}, nonce=nonce)

    def read_json(self, limit: int | None = None) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise APIError("حجم الطلب غير صالح.", 400)
        if length > (limit or MAX_BODY):
            raise APIError("الطلب أكبر من الحد المسموح.", 413)
        raw = self.rfile.read(max(0, length))
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise APIError("تعذر قراءة بيانات الطلب.", 400)
        if not isinstance(data, dict):
            raise APIError("صيغة بيانات الطلب غير صالحة.", 400)
        return data

    def get_session(self) -> tuple[str | None, dict | None]:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            return None, None
        morsel = cookie.get("fakhama_admin")
        if not morsel:
            return None, None
        token = morsel.value
        session = SESSIONS.get(token)
        if not session or session["expires"] < time.time():
            SESSIONS.pop(token, None)
            return token, None
        return token, session

    def require_admin(self, write: bool = False) -> dict | None:
        token, session = self.get_session()
        if not session:
            self.send_json({"error": "يلزم تسجيل الدخول للمتابعة."}, 401)
            return None
        if write and not hmac.compare_digest(str(session["csrf"]), str(self.headers.get("X-CSRF-Token", ""))):
            self.send_json({"error": "انتهت صلاحية الجلسة. حدّث الصفحة ثم سجّل الدخول مجددًا."}, 403)
            return None
        return {"token": token, **session}

    def _run(self) -> None:
        self.request_id = uuid.uuid4().hex[:16]
        self.started = time.time()
        self.status_sent = 0
        self.audited = False
        self.audit_detail = ""
        try:
            self.route()
        except APIError as exc:
            self.send_json({"error": exc.message, **exc.extra}, exc.status, exc.headers)
        except BrokenPipeError:
            pass
        except Exception:
            traceback.print_exc()
            try:
                self.send_json({"error": "حدث خطأ غير متوقع في الخادم."}, 500)
            except Exception:
                pass

    def do_GET(self): self._run()
    def do_POST(self): self._run()
    def do_PUT(self): self._run()
    def do_PATCH(self): self._run()
    def do_DELETE(self): self._run()
    def do_HEAD(self): self._run()

    def create_order(self) -> None:
        # Idempotency-Key: a retry of the same submission (flaky mobile network) returns the first result instead of
        # creating a second order.
        key = self.headers.get("Idempotency-Key", "").strip()
        if key and not re.fullmatch(r"[A-Za-z0-9_\-]{8,64}", key):
            raise APIError("مفتاح التكرار غير صالح.")
        now = time.time()
        for stale in [k for k, (exp, _) in IDEMPOTENT_ORDERS.items() if exp < now]:
            IDEMPOTENT_ORDERS.pop(stale, None)
        if key and key in IDEMPOTENT_ORDERS:
            self.send_json(IDEMPOTENT_ORDERS[key][1], 200, {"Idempotent-Replayed": "true"}); return
        if not allow_attempt(ORDER_ATTEMPTS, self.client_ip(), *ORDER_LIMIT):
            raise APIError("تم إرسال طلبات كثيرة خلال وقت قصير. حاول بعد قليل أو تواصل مع المتجر هاتفيًا.", 429, {"Retry-After": str(ORDER_LIMIT[1])})
        data = self.read_json()
        if data.get("consent") is not True:
            raise APIError("يلزم الموافقة على سياسة الخصوصية لإرسال الطلب.")
        name = str(data.get("name", "")).strip()[:120]
        phone = str(data.get("phone", "")).strip()[:30]
        city = str(data.get("city", "")).strip()[:100]
        address = str(data.get("address", "")).strip()[:250]
        notes = str(data.get("note", data.get("notes", ""))).strip()[:1000]
        digits = re.sub(r"\D", "", phone)
        if len(name) < 2: raise APIError("اكتب الاسم كاملًا.")
        if len(digits) < 8 or len(digits) > 15: raise APIError("تحقق من رقم الجوال.")
        if len(city) < 2: raise APIError("اكتب المدينة.")
        items = data.get("items")
        if not isinstance(items, list) or not items or len(items) > 40:
            raise APIError("أضف منتجًا واحدًا على الأقل إلى السلة.")
        quantities: dict[int, int] = {}
        for item in items:
            if not isinstance(item, dict): raise APIError("بيانات المنتجات غير صحيحة.")
            product_id = int_field(item.get("product_id", item.get("id")), "المنتج", 1, 2_000_000_000)
            quantity = int_field(item.get("quantity", item.get("qty")), "الكمية", 1, 50)
            quantities[product_id] = quantities.get(product_id, 0) + quantity
        with connect_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            subtotal = 0
            products = []
            for product_id, quantity in quantities.items():
                row = conn.execute("SELECT id,name,sku,price,stock,active FROM products WHERE id=?", (product_id,)).fetchone()
                if not row or not row["active"]: raise APIError("أحد المنتجات لم يعد متاحًا. حدّث الصفحة وحاول مجددًا.", 409)
                if row["stock"] < quantity: raise APIError(f"الكمية المتاحة من «{row['name']}» هي {row['stock']} فقط.", 409)
                subtotal += row["price"] * quantity
                products.append((row, quantity))
            settings = get_settings(conn)
            delivery_fee = int(settings.get("delivery_fee", "0") or 0)
            total = subtotal + delivery_fee
            created = now_iso()
            cursor = conn.execute("""INSERT INTO orders(order_number,customer_name,phone,city,address,notes,status,subtotal,delivery_fee,total,created_at,updated_at,consent_at)
                                      VALUES('PENDING',?,?,?,?,?,'new',?,?,?,?,?,?)""",
                                  (name, phone, city, address, notes, subtotal, delivery_fee, total, created, created, created))
            order_id = cursor.lastrowid
            order_number = f"MH-{datetime.now().strftime('%y%m%d')}-{order_id:05d}"
            conn.execute("UPDATE orders SET order_number=? WHERE id=?", (order_number, order_id))
            for row, quantity in products:
                conn.execute("INSERT INTO order_items(order_id,product_id,product_name,sku,unit_price,quantity) VALUES(?,?,?,?,?,?)",
                             (order_id, row["id"], row["name"], row["sku"], row["price"], quantity))
                conn.execute("UPDATE products SET stock=stock-?,updated_at=? WHERE id=?", (quantity, created, row["id"]))
        result = {"ok": True, "order_id": order_id, "order_number": order_number, "subtotal": subtotal,
                  "delivery_fee": delivery_fee, "total": total}
        if key:
            IDEMPOTENT_ORDERS[key] = (time.time() + IDEMPOTENCY_TTL, result)
        self.send_json(result, 201)

    def export_orders(self) -> None:
        with connect_db() as conn:
            rows = conn.execute("SELECT * FROM orders ORDER BY id DESC").fetchall()
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["رقم الطلب", "الاسم", "الجوال", "المدينة", "الحالة", "المجموع", "التوصيل", "الإجمالي", "تاريخ الإنشاء", "ملاحظات"])
        for row in rows:
            writer.writerow([row["order_number"], row["customer_name"], row["phone"], row["city"], ORDER_STATUSES.get(row["status"], row["status"]), row["subtotal"], row["delivery_fee"], row["total"], row["created_at"], row["notes"]])
        body = ("\ufeff" + output.getvalue()).encode("utf-8")
        self._send(200, body, "text/csv; charset=utf-8", {"Content-Disposition": "attachment; filename=orders.csv"})

    def resolve_static(self, path: str) -> tuple[Path, str | None] | None:
        """Map a URL path to a file. Only whitelisted pages and the assets/uploads/icons folders are reachable."""
        if "\x00" in path or "\\" in path:
            return None
        if path in PUBLIC_FILES:
            name, content_type = PUBLIC_FILES[path]
            return BASE_DIR / name, content_type
        if path == "/.well-known/assetlinks.json":
            # Only needed if a Trusted Web Activity build is ever added; supply the file via DIGITAL_ASSET_LINKS_FILE.
            configured = os.environ.get("DIGITAL_ASSET_LINKS_FILE", "")
            return (Path(configured), "application/json") if configured else None
        for prefix, root in (("/assets/", ASSETS_DIR), ("/uploads/", UPLOAD_DIR), ("/icons/", ICONS_DIR)):
            if path.startswith(prefix):
                target = (root / path[len(prefix):]).resolve()
                # Must stay inside this one folder: "/assets/../store.db" or "/assets/%2e%2e/app.py" resolve elsewhere.
                if not target.is_relative_to(root.resolve()):
                    return None
                return target, None
        return None

    def serve_index(self, slug: str | None) -> None:
        """The storefront page with its <head> written for this URL (storefront, or one product for /product/<slug>)."""
        template = (BASE_DIR / "index.html").read_text(encoding="utf-8")
        base = self.base_url()
        with connect_db() as conn:
            settings = get_settings(conn)
            product = None
            if slug:
                row = conn.execute("SELECT p.*,c.name AS category_name FROM products p JOIN categories c ON c.slug=p.category "
                                   "WHERE p.slug=? AND p.active=1 AND c.active=1", (slug,)).fetchone()
                if row:
                    product = product_dict(row, row["category_name"], images_for(conn, [row["id"]])[row["id"]])
        ready = settings.get("site_ready", "0") == "1"
        store = settings.get("store_name") or seo.SITE_NAME_FALLBACK
        robots = "index,follow,max-image-preview:large" if ready else "noindex,nofollow"
        status = 200
        if product:
            summary = re.sub(r"\s+", " ", product["description"]).strip()[:155] or settings.get("seo_description", "")
            image = seo.absolute(base, product["image"] or DEFAULT_SHARE_IMAGE)
            page = seo.render_page(
                template, title=f"{product['name']} | {store}", description=summary, robots=robots,
                canonical=(base + seo.product_path(product["slug"])) if ready else "", og_url=base + seo.product_path(product["slug"]),
                og_image=image, og_type="product", schemas=seo.product_schema(product, settings, base), site_name=store,
                image_alt=product.get("alt") or product["name"])
        else:
            if slug:
                status, robots = 404, "noindex,nofollow"
            page = seo.render_page(
                template, title=settings.get("seo_title") or store, description=settings.get("seo_description", ""), robots=robots,
                canonical=(base + "/") if (ready and not slug) else "", og_url=base + "/" if ready else "",
                og_image=seo.absolute(base, DEFAULT_SHARE_IMAGE), og_type="website",
                schemas=[seo.store_schema(settings, base, ready, DEFAULT_SHARE_IMAGE), seo.website_schema(settings, base)],
                site_name=store, preload_image=None if slug else DEFAULT_SHARE_IMAGE, image_alt=store)
        self.render_html(page, status)

    def serve_static(self, path: str) -> None:
        if path in ("/", "/index.html"):
            return self.serve_index(None)
        product_match = re.fullmatch(r"/product/([^/]+)", path)
        if product_match:
            return self.serve_index(product_match.group(1))
        if path in legal.PAGES:
            with connect_db() as conn:
                return self.render_html(legal.render(path, get_settings(conn)))
        if path in ("/admin", "/admin/", "/admin.html"):
            return self.render_html((BASE_DIR / "admin.html").read_text(encoding="utf-8"))
        if path == "/offline.html":
            return self.render_html((BASE_DIR / "offline.html").read_text(encoding="utf-8"))
        found = self.resolve_static(path)
        if not found or not found[0].is_file():
            self._send(404, b"Not found", "text/plain; charset=utf-8"); return
        target, forced_type = found
        content_type = forced_type or mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in ("application/javascript", "application/json", "application/manifest+json"):
            content_type += "; charset=utf-8"
        try:
            body = target.read_bytes()
            stat = target.stat()
        except OSError:
            self._send(500, b"Unable to read file", "text/plain; charset=utf-8"); return
        headers = {"Last-Modified": formatdate(stat.st_mtime, usegmt=True), "ETag": f'W/"{stat.st_size}-{stat.st_mtime_ns}"'}
        if path.startswith("/uploads/"):
            headers["Cache-Control"] = "public, max-age=31536000, immutable"   # uploads get a random name: never reused
        elif path.startswith("/assets/fonts/"):
            headers["Cache-Control"] = "public, max-age=31536000, immutable"   # font files are never edited in place
        elif path.startswith(("/assets/", "/icons/")):
            headers["Cache-Control"] = "public, max-age=86400"
        else:
            headers["Cache-Control"] = "no-cache"
        if path == "/sw.js":
            headers["Service-Worker-Allowed"] = "/"
        self._send(200, body, content_type, headers)

    def handle_login(self, data: dict) -> None:
        username = str(data.get("username", "")).strip()[:100]
        password = str(data.get("password", ""))[:200]
        code = str(data.get("code", ""))[:12]
        ip = self.client_ip()
        now = time.time()
        recent = [t for t in LOGIN_FAILURES.get(ip, []) if now - t < 300]
        if len(recent) >= 8:
            raise APIError("محاولات كثيرة. انتظر بضع دقائق ثم حاول مرة أخرى.", 429, {"Retry-After": "300"})
        with connect_db() as conn:
            admin = conn.execute("SELECT username,password_hash,totp_secret FROM admins WHERE username=?", (username,)).fetchone()
        if not admin or not verify_password(password, admin["password_hash"]):
            recent.append(now); LOGIN_FAILURES[ip] = recent
            self.log_admin(username or "?", "login_failed", "wrong username or password")
            raise APIError("اسم المستخدم أو كلمة المرور غير صحيحة.", 401)
        if admin["totp_secret"]:
            # Second factor: password alone is not enough once the admin enabled an authenticator app.
            if not code:
                raise APIError("أدخل رمز التحقق من تطبيق المصادقة.", 401, extra={"need_code": True})
            if not totp.verify(admin["totp_secret"], code):
                recent.append(now); LOGIN_FAILURES[ip] = recent
                self.log_admin(username, "login_failed", "wrong 2FA code")
                raise APIError("رمز التحقق غير صحيح أو منتهٍ.", 401, extra={"need_code": True})
        LOGIN_FAILURES.pop(ip, None)
        purge_expired(SESSIONS, 0, key_expires=True)
        token = secrets.token_urlsafe(36)
        csrf = secrets.token_urlsafe(24)
        SESSIONS[token] = {"username": admin["username"], "csrf": csrf, "expires": now + SESSION_TTL}
        self.log_admin(admin["username"], "login", "2FA" if admin["totp_secret"] else "password")
        self.send_json({"ok": True, "username": admin["username"], "csrf": csrf}, 200,
                       {"Set-Cookie": [self.session_cookie(token, SESSION_TTL), self.flag_cookie(SESSION_TTL)]})

    AUDIT_ACTIONS = ((re.compile(r"/api/admin/settings"), "settings_update"), (re.compile(r"/api/admin/password"), "password_change"),
                     (re.compile(r"/api/admin/2fa/(setup|enable|disable)"), "two_factor"), (re.compile(r"/api/admin/categories"), "category_change"),
                     (re.compile(r"/api/admin/orders/\d+"), "order_status"))

    def audit_admin_change(self) -> None:
        """Record successful admin writes that are not already audited inside their own handler."""
        path = unquote(urlsplit(self.path).path)
        if self.audited or self.command not in ("POST", "PUT", "PATCH", "DELETE") or not path.startswith("/api/admin/") or not (0 < self.status_sent < 400):
            return
        self.audited = True
        for pattern, action in self.AUDIT_ACTIONS:
            m = pattern.fullmatch(path) or (pattern.match(path) if "categories" in action else None)
            if m:
                _, session = self.get_session()
                actor = session["username"] if session else "admin"
                detail = self.audit_detail or f"{self.command} {path}"
                self.log_admin(actor, action, detail)
                return

    def log_admin(self, actor: str, action: str, detail: str = "") -> None:
        try:
            with connect_db() as conn:
                audit(conn, actor, action, detail, self.client_ip())
        except Exception:
            traceback.print_exc()  # auditing must never break the action itself

    def flag_cookie(self, max_age: int) -> str:
        """Readable marker (no secret) that lets the storefront show its admin bar without probing the API."""
        secure = "; Secure" if self.is_secure() else ""
        return f"fakhama_admin_flag={'1' if max_age else ''}; Path=/; SameSite=Lax; Max-Age={max_age}{secure}"

    def session_cookie(self, value: str, max_age: int) -> str:
        secure = "; Secure" if self.is_secure() else ""
        return f"fakhama_admin={value}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}{secure}"

    def save_product(self, product_id: int | None, data: dict, session_user: str = "admin") -> None:
        name = str(data.get("name", "")).strip()[:140]
        category = str(data.get("category", "")).strip()[:100]
        description = str(data.get("description", "")).strip()[:4000]
        alt = str(data.get("alt", "")).strip()[:250]
        badge = str(data.get("badge", "")).strip()[:60]
        sku = str(data.get("sku", "")).strip()[:80]
        price = int_field(data.get("price"), "السعر", 0, 100_000_000)  # 0 = price on request
        stock = int_field(data.get("stock", 0), "المخزون", 0, 1_000_000)
        if len(name) < 2: raise APIError("اسم المنتج مطلوب.")
        if not category: raise APIError("اختر قسمًا للمنتج.")
        if alt == "": alt = name
        with connect_db() as conn:
            cat = conn.execute("SELECT slug FROM categories WHERE slug=? AND active=1", (category,)).fetchone()
            if not cat: raise APIError("القسم المحدد غير موجود أو غير مفعّل.")
            old = conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone() if product_id else None
            if product_id and not old: raise APIError("المنتج غير موجود.", 404)
            # Gallery API: "images" = photos to keep (in display order), "new_images" = uploads [{data, thumb}].
            # The first photo is the primary one. Legacy clients may still send a single "image" / "image_data",
            # which replaces the primary photo as before.
            existing_rows = conn.execute("SELECT path,thumb FROM product_images WHERE product_id=? ORDER BY position,id", (product_id,)).fetchall() if old else []
            existing = {public_image(r["path"]): r["thumb"] for r in existing_rows}
            def usable(url) -> str:
                url = public_image(str(url or "").strip())
                return url if url and (url in existing or image_file_exists(url)) else ""
            new_images = data.get("new_images") or []
            if not isinstance(new_images, list): raise APIError("صيغة الصور غير صحيحة.")
            if isinstance(data.get("images"), list) or new_images:
                kept = data["images"] if isinstance(data.get("images"), list) else [r["path"] for r in existing_rows]
                gallery = []
                for entry in kept:
                    url = usable(entry.get("url") if isinstance(entry, dict) else entry)
                    if url and url not in [g[0] for g in gallery]:
                        sent_thumb = public_image(str(entry.get("thumb") or "")) if isinstance(entry, dict) else ""
                        gallery.append((url, existing.get(url) or (sent_thumb if sent_thumb and image_file_exists(sent_thumb) else "") or thumb_for(url)))
                for item in new_images[:MAX_GALLERY]:
                    if not isinstance(item, dict) or not item.get("data"): raise APIError("صيغة الصور غير صحيحة.")
                    gallery.append((save_image_data(item["data"]), save_image_data(item["thumb"]) if item.get("thumb") else ""))
            elif data.get("image_data"):
                gallery = [(save_image_data(data["image_data"]), "")]
            elif usable(data.get("image")):
                url = usable(data["image"]); gallery = [(url, existing.get(url) or thumb_for(url))]
            else:
                gallery = [(public_image(r["path"]), r["thumb"]) for r in existing_rows]
            if not gallery: raise APIError("أضف صورة للمنتج.")
            if len(gallery) > MAX_GALLERY: raise APIError(f"الحد الأقصى {MAX_GALLERY} صورة لكل منتج.")
            image = gallery[0][0]
            slug_base = slugify(str(data.get("slug", name)))
            slug = slug_base
            n = 2
            while True:
                conflict = conn.execute("SELECT id FROM products WHERE slug=?", (slug,)).fetchone()
                if not conflict or (product_id and conflict["id"] == product_id): break
                slug = f"{slug_base}-{n}"; n += 1
            featured = 1 if data.get("featured") else 0
            active = 1 if data.get("active", True) else 0
            ts = now_iso()
            if old:
                conn.execute("""UPDATE products SET name=?,slug=?,category=?,description=?,price=?,image=?,alt=?,badge=?,sku=?,stock=?,featured=?,active=?,updated_at=? WHERE id=?""",
                             (name, slug, category, description, price, image, alt, badge, sku, stock, featured, active, ts, product_id))
                result_id = product_id
            else:
                cur = conn.execute("""INSERT INTO products(name,slug,category,description,price,image,alt,badge,sku,stock,featured,active,created_at,updated_at)
                                      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                   (name, slug, category, description, price, image, alt, badge, sku, stock, featured, active, ts, ts))
                result_id = cur.lastrowid
            previous = [r["path"] for r in conn.execute("SELECT path FROM product_images WHERE product_id=? UNION SELECT thumb FROM product_images WHERE product_id=?", (result_id, result_id))]
            conn.execute("DELETE FROM product_images WHERE product_id=?", (result_id,))
            for position, (url, thumb) in enumerate(gallery):
                conn.execute("INSERT INTO product_images(product_id,path,thumb,position) VALUES(?,?,?,?)", (result_id, url, thumb, position))
            drop_unreferenced_uploads(conn, previous)
            row = conn.execute("SELECT p.*,c.name AS category_name FROM products p JOIN categories c ON c.slug=p.category WHERE p.id=?", (result_id,)).fetchone()
            product = product_dict(row, row["category_name"], images_for(conn, [result_id])[result_id])
            audit(conn, session_user, "product_update" if product_id else "product_create", f"#{result_id} {name} ({len(gallery)} images)", self.client_ip())
        self.send_json({"ok": True, "product": product}, 200 if product_id else 201)

    def route_mutation(self) -> None:
        path = unquote(urlsplit(self.path).path)
        if path == "/api/admin/login" and self.command == "POST":
            self.handle_login(self.read_json()); return
        if path == "/api/admin/logout" and self.command == "POST":
            session = self.require_admin(write=True)
            if not session: return
            SESSIONS.pop(session["token"], None)
            self.log_admin(session["username"], "logout")
            self.send_json({"ok": True}, 200, {"Set-Cookie": [self.session_cookie("", 0), self.flag_cookie(0)]}); return
        if path == "/api/admin/upload" and self.command == "POST":
            if not self.require_admin(write=True): return
            data = self.read_json(MAX_UPLOAD_BODY)
            url = save_image_data(data.get("data"))
            thumb = save_image_data(data["thumb"]) if data.get("thumb") else ""
            self.send_json({"ok": True, "url": url, "thumb": thumb}, 201); return
        if path == "/api/admin/products" and self.command == "POST":
            current = self.require_admin(write=True)
            if not current: return
            current_admin = current["username"]
            self.save_product(None, self.read_json(MAX_UPLOAD_BODY), current_admin); return
        match = re.fullmatch(r"/api/admin/products/(\d+)", path)
        if match and self.command == "PUT":
            current = self.require_admin(write=True)
            if not current: return
            current_admin = current["username"]
            self.save_product(int(match.group(1)), self.read_json(MAX_UPLOAD_BODY), current_admin); return
        if match and self.command == "DELETE":
            current = self.require_admin(write=True)
            if not current: return
            product_id = int(match.group(1))
            with connect_db() as conn:
                owned = [r[0] for r in conn.execute("SELECT path FROM product_images WHERE product_id=? UNION SELECT thumb FROM product_images WHERE product_id=?", (product_id, product_id))]
                cur = conn.execute("DELETE FROM products WHERE id=?", (product_id,))
                if not cur.rowcount: raise APIError("المنتج غير موجود.", 404)
                drop_unreferenced_uploads(conn, owned)
                audit(conn, current["username"], "product_delete", f"#{product_id}", self.client_ip())
            self.send_json({"ok": True}); return
        if path == "/api/admin/categories" and self.command == "POST":
            if not self.require_admin(write=True): return
            data = self.read_json(MAX_UPLOAD_BODY); name = str(data.get("name", "")).strip()[:80]
            slug = slugify(str(data.get("slug") or name))
            if len(name) < 2: raise APIError("اسم القسم مطلوب.")
            with connect_db() as conn:
                if conn.execute("SELECT 1 FROM categories WHERE slug=?", (slug,)).fetchone(): raise APIError("يوجد قسم بهذا الرابط بالفعل.", 409)
                sort = conn.execute("SELECT COALESCE(MAX(sort_order),0)+1 FROM categories").fetchone()[0]
                image = save_image_data(data["image_data"]) if data.get("image_data") else (public_image(str(data.get("image"))) if image_file_exists(public_image(str(data.get("image") or ""))) else "")
                conn.execute("INSERT INTO categories(slug,name,sort_order,active,image) VALUES(?,?,?,1,?)", (slug, name, sort, image))
            self.send_json({"ok": True, "slug": slug, "name": name, "image": image}, 201); return
        if path == "/api/admin/categories/reorder" and self.command == "POST":
            if not self.require_admin(write=True): return
            order = self.read_json().get("order")
            if not isinstance(order, list) or not all(isinstance(x, str) for x in order): raise APIError("ترتيب الأقسام غير صالح.")
            with connect_db() as conn:
                known = {r["slug"] for r in conn.execute("SELECT slug FROM categories")}
                if set(order) != known: raise APIError("قائمة الأقسام لا تطابق الأقسام الحالية؛ حدّث الصفحة.", 409)
                for position, slug in enumerate(order, 1):
                    conn.execute("UPDATE categories SET sort_order=? WHERE slug=?", (position, slug))
            self.send_json({"ok": True}); return
        match = re.fullmatch(r"/api/admin/categories/([\w\-]+)", path, re.UNICODE)
        if match and self.command == "PUT":
            if not self.require_admin(write=True): return
            slug = match.group(1)
            data = self.read_json(MAX_UPLOAD_BODY); name = str(data.get("name", "")).strip()[:80]
            active = 1 if data.get("active", True) else 0
            if len(name) < 2: raise APIError("اسم القسم مطلوب.")
            with connect_db() as conn:
                row = conn.execute("SELECT image FROM categories WHERE slug=?", (slug,)).fetchone()
                if not row: raise APIError("القسم غير موجود.", 404)
                image = row["image"]
                if data.get("image_data"): image = save_image_data(data["image_data"])
                elif data.get("remove_image"): image = ""
                elif image_file_exists(public_image(str(data.get("image") or ""))): image = public_image(str(data["image"]))
                conn.execute("UPDATE categories SET name=?,active=?,image=? WHERE slug=?", (name, active, image, slug))
                if image != row["image"]: drop_unreferenced_uploads(conn, [row["image"]])
            self.send_json({"ok": True, "slug": slug, "name": name, "active": bool(active), "image": image}); return
        if match and self.command == "DELETE":
            if not self.require_admin(write=True): return
            slug = match.group(1)
            with connect_db() as conn:
                count = conn.execute("SELECT COUNT(*) FROM products WHERE category=?", (slug,)).fetchone()[0]
                if count: raise APIError("انقل منتجات هذا القسم أو احذفها قبل حذف القسم.", 409)
                old_image = (conn.execute("SELECT image FROM categories WHERE slug=?", (slug,)).fetchone() or {"image": ""})["image"]
                cur = conn.execute("DELETE FROM categories WHERE slug=?", (slug,))
                if not cur.rowcount: raise APIError("القسم غير موجود.", 404)
                drop_unreferenced_uploads(conn, [old_image])
            self.send_json({"ok": True}); return
        if path == "/api/admin/settings" and self.command == "PUT":
            if not self.require_admin(write=True): return
            data = self.read_json()
            allowed = {"store_name": 100, "tagline": 160, "city": 100, "phone": 40, "whatsapp": 40, "address": 250, "map_url": 400, "delivery_note": 400, "instagram": 250, "seo_title": 180, "seo_description": 320, "hero_title": 180, "hero_copy": 600}
            for url_key in ("map_url", "instagram"):
                # These values end up in href attributes on the storefront; allow web links only (no javascript: etc.).
                if str(data.get(url_key, "")).strip() and not re.match(r"https?://[^\s]+$", str(data[url_key]).strip(), re.I):
                    raise APIError("الروابط يجب أن تبدأ بـ https:// أو http://")
            with connect_db() as conn:
                for key, limit in allowed.items():
                    if key in data:
                        val = str(data[key]).strip()[:limit]
                        conn.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, val))
                if "site_ready" in data:
                    ready = "1" if data["site_ready"] else "0"
                    conn.execute("INSERT INTO settings(key,value) VALUES('site_ready',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (ready,))
                if "delivery_fee" in data:
                    fee = int_field(data["delivery_fee"], "رسوم التوصيل", 0, 10_000_000)
                    conn.execute("INSERT INTO settings(key,value) VALUES('delivery_fee',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(fee),))
                settings = get_settings(conn)
            self.send_json({"ok": True, "settings": settings}); return
        if path == "/api/admin/2fa/setup" and self.command == "POST":
            session = self.require_admin(write=True)
            if not session: return
            with connect_db() as conn:
                row = conn.execute("SELECT totp_secret FROM admins WHERE username=?", (session["username"],)).fetchone()
                store = get_settings(conn).get("store_name") or seo.SITE_NAME_FALLBACK
            if row and row["totp_secret"]: raise APIError("التحقق بخطوتين مفعّل بالفعل.", 409)
            secret = totp.new_secret()
            SESSIONS[session["token"]]["totp_pending"] = secret
            self.send_json({"secret": secret, "otpauth": totp.otpauth_uri(secret, session["username"], store)}); return
        if path == "/api/admin/2fa/enable" and self.command == "POST":
            session = self.require_admin(write=True)
            if not session: return
            pending = SESSIONS.get(session["token"], {}).get("totp_pending")
            if not pending: raise APIError("ابدأ الإعداد أولًا.", 409)
            if not totp.verify(pending, str(self.read_json().get("code", ""))): raise APIError("رمز التحقق غير صحيح. تأكد من ساعة الهاتف وحاول مجددًا.")
            with connect_db() as conn:
                conn.execute("UPDATE admins SET totp_secret=?,updated_at=? WHERE username=?", (pending, now_iso(), session["username"]))
            SESSIONS[session["token"]].pop("totp_pending", None)
            self.send_json({"ok": True}); return
        if path == "/api/admin/2fa/disable" and self.command == "POST":
            session = self.require_admin(write=True)
            if not session: return
            data = self.read_json()
            with connect_db() as conn:
                row = conn.execute("SELECT password_hash,totp_secret FROM admins WHERE username=?", (session["username"],)).fetchone()
                if not row or not row["totp_secret"]: raise APIError("التحقق بخطوتين غير مفعّل.", 409)
                if not verify_password(str(data.get("password", "")), row["password_hash"]) or not totp.verify(row["totp_secret"], str(data.get("code", ""))):
                    raise APIError("كلمة المرور أو الرمز غير صحيح.", 403)
                conn.execute("UPDATE admins SET totp_secret='',updated_at=? WHERE username=?", (now_iso(), session["username"]))
            self.send_json({"ok": True}); return
        if path == "/api/admin/password" and self.command == "POST":
            session = self.require_admin(write=True)
            if not session: return
            data = self.read_json(); old = str(data.get("current_password", "")); new = str(data.get("new_password", ""))
            if len(new) < 12: raise APIError("كلمة المرور الجديدة يجب ألا تقل عن 12 خانة.")
            with connect_db() as conn:
                row = conn.execute("SELECT password_hash FROM admins WHERE username=?", (session["username"],)).fetchone()
                if not row or not verify_password(old, row["password_hash"]): raise APIError("كلمة المرور الحالية غير صحيحة.", 403)
                conn.execute("UPDATE admins SET password_hash=?,updated_at=? WHERE username=?", (hash_password(new), now_iso(), session["username"]))
            # A new password revokes every other signed-in device; only the session that made the change survives.
            for token in [t for t, s in SESSIONS.items() if s["username"] == session["username"] and t != session["token"]]:
                SESSIONS.pop(token, None)
            self.send_json({"ok": True}); return
        if path.startswith("/api/admin/"):
            if self.command == "GET":
                self.send_json({"error": "غير مصرح."}, 401)
            else:
                self.send_json({"error": "المسار أو الطريقة غير مدعومين."}, 404)
            return
        self.send_json({"error": "المسار غير موجود."}, 404)

    def route_patch(self) -> None:
        path = urlsplit(self.path).path
        match = re.fullmatch(r"/api/admin/orders/(\d+)", path)
        if not match:
            self.send_json({"error": "المسار غير موجود."}, 404); return
        if not self.require_admin(write=True): return
        data = self.read_json(); new_status = str(data.get("status", ""))
        if new_status not in ORDER_STATUSES: raise APIError("حالة الطلب غير صالحة.")
        order_id = int(match.group(1))
        with connect_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            order = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
            if not order: raise APIError("الطلب غير موجود.", 404)
            old_status = order["status"]
            lines = conn.execute("SELECT product_id,quantity FROM order_items WHERE order_id=?", (order_id,)).fetchall()
            if old_status != "cancelled" and new_status == "cancelled":
                for line in lines:
                    if line["product_id"]:
                        conn.execute("UPDATE products SET stock=stock+?,updated_at=? WHERE id=?", (line["quantity"], now_iso(), line["product_id"]))
            elif old_status == "cancelled" and new_status != "cancelled":
                for line in lines:
                    if line["product_id"]:
                        stock = conn.execute("SELECT stock,name FROM products WHERE id=?", (line["product_id"],)).fetchone()
                        if stock and stock["stock"] < line["quantity"]:
                            raise APIError(f"لا يمكن إعادة تفعيل الطلب؛ مخزون «{stock['name']}» غير كافٍ.", 409)
                        if stock:
                            conn.execute("UPDATE products SET stock=stock-?,updated_at=? WHERE id=?", (line["quantity"], now_iso(), line["product_id"]))
            conn.execute("UPDATE orders SET status=?,updated_at=? WHERE id=?", (new_status, now_iso(), order_id))
            self.audit_detail = f"#{order_id}: {old_status} → {new_status}"
            updated = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
            result = order_dict(conn, updated, True)
        self.send_json({"ok": True, "order": result})

    def route(self) -> None:  # override dispatch to keep verb-specific mutations explicit
        parsed_path = urlsplit(self.path).path
        if self.command in ("POST", "PUT", "DELETE"):
            # Public order creation is the only unauthenticated write route.
            if parsed_path == "/api/orders" and self.command == "POST":
                try: self.create_order()
                except APIError as exc: self.send_json({"error": exc.message, **exc.extra}, exc.status, exc.headers)
                return
            try: self.route_mutation()
            except APIError as exc: self.send_json({"error": exc.message, **exc.extra}, exc.status, exc.headers)
            return
        if self.command == "PATCH":
            try: self.route_patch()
            except APIError as exc: self.send_json({"error": exc.message, **exc.extra}, exc.status, exc.headers)
            return
        self.route_read_only()

    def route_read_only(self) -> None:
        # Reuse GET routes from route(), while preventing recursive dispatch.
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        if path in ("/api/health", "/api/store", "/api/categories", "/api/products", "/api/admin/me", "/api/admin/dashboard", "/api/admin/products", "/api/admin/categories", "/api/admin/orders", "/api/admin/settings", "/api/admin/export/orders.csv", "/robots.txt", "/sitemap.xml") or path.startswith("/"):
            # The GET/HEAD read router below duplicates the GET route table deliberately.
            try:
                if path == "/api/health":
                    try:
                        with connect_db() as conn: conn.execute("SELECT 1").fetchone()
                    except Exception:
                        self.send_json({"ok": False, "service": "fakhama-store", "version": VERSION, "db": False}, 503); return
                    self.send_json({"ok": True, "service": "fakhama-store", "version": VERSION, "db": True, "time": now_iso()}); return
                if path == "/api/store":
                    with connect_db() as conn: settings = get_settings(conn)
                    self.send_json({"name": settings["store_name"], "tagline": settings["tagline"], "city": settings["city"], "phone": settings["phone"], "whatsapp": settings["whatsapp"], "address": settings["address"], "map_url": settings.get("map_url", ""), "delivery_fee": int(settings.get("delivery_fee", "0") or 0), "delivery_note": settings.get("delivery_note", ""), "site_ready": settings.get("site_ready", "0") == "1", "seo_title": settings.get("seo_title", ""), "seo_description": settings.get("seo_description", ""), "hero_title": settings.get("hero_title", ""), "hero_copy": settings.get("hero_copy", "")}, revalidate=True); return
                if path == "/api/categories":
                    with connect_db() as conn: rows = conn.execute("SELECT slug,name,sort_order,image FROM categories WHERE active=1 ORDER BY sort_order,name").fetchall()
                    self.send_json({"categories": [{**dict(r), "image": public_image(r["image"])} for r in rows]}, revalidate=True); return
                if path == "/api/products":
                    with connect_db() as conn:
                        rows = conn.execute("SELECT p.*,c.name AS category_name FROM products p JOIN categories c ON c.slug=p.category WHERE p.active=1 AND c.active=1 ORDER BY p.featured DESC,p.id DESC").fetchall()
                        gallery = images_for(conn, [r["id"] for r in rows])
                    self.send_json({"products": [product_dict(r, r["category_name"], gallery[r["id"]]) for r in rows]}, revalidate=True); return
                if path == "/api/admin/me":
                    session = self.require_admin()
                    if session:
                        with connect_db() as conn:
                            row = conn.execute("SELECT totp_secret FROM admins WHERE username=?", (session["username"],)).fetchone()
                        self.send_json({"authenticated": True, "username": session["username"], "csrf": session["csrf"], "totp_enabled": bool(row and row["totp_secret"])})
                    return
                if path == "/api/admin/dashboard":
                    if not self.require_admin(): return
                    with connect_db() as conn:
                        total_products = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
                        active_products = conn.execute("SELECT COUNT(*) FROM products WHERE active=1").fetchone()[0]
                        pending = conn.execute("SELECT COUNT(*) FROM orders WHERE status='new'").fetchone()[0]
                        total_orders = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
                        delivered_revenue = conn.execute("SELECT COALESCE(SUM(total),0) FROM orders WHERE status='delivered'").fetchone()[0]
                        low_stock = conn.execute("SELECT COUNT(*) FROM products WHERE active=1 AND stock<=3").fetchone()[0]
                        recent_rows = conn.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 6").fetchall()
                        recent = [order_dict(conn, r, False) for r in recent_rows]
                        since = (datetime.now(timezone.utc) - timedelta(days=13)).strftime("%Y-%m-%d")
                        per_day = {r["d"]: (r["n"], r["t"]) for r in conn.execute(
                            "SELECT substr(created_at,1,10) AS d, COUNT(*) AS n, COALESCE(SUM(total),0) AS t FROM orders WHERE status!='cancelled' AND substr(created_at,1,10)>=? GROUP BY d", (since,))}
                        daily = []
                        for offset in range(13, -1, -1):
                            day = (datetime.now(timezone.utc) - timedelta(days=offset)).strftime("%Y-%m-%d")
                            daily.append({"date": day, "orders": per_day.get(day, (0, 0))[0], "total": per_day.get(day, (0, 0))[1]})
                        low_items = [dict(r) for r in conn.execute("SELECT id,name,stock FROM products WHERE active=1 AND stock<=3 ORDER BY stock,id LIMIT 8")]
                    self.send_json({"products": total_products, "active_products": active_products, "pending_orders": pending, "total_orders": total_orders, "delivered_revenue": delivered_revenue, "low_stock": low_stock, "recent_orders": recent, "daily": daily, "low_stock_items": low_items}); return
                if path == "/api/admin/audit":
                    if not self.require_admin(): return
                    with connect_db() as conn:
                        rows = conn.execute("SELECT at,actor,action,detail,ip FROM audit_log ORDER BY id DESC LIMIT 100").fetchall()
                    self.send_json({"entries": [dict(r) for r in rows]}); return
                if path == "/api/admin/products":
                    if not self.require_admin(): return
                    with connect_db() as conn:
                        rows = conn.execute("SELECT p.*,c.name AS category_name FROM products p LEFT JOIN categories c ON c.slug=p.category ORDER BY p.id DESC").fetchall()
                        gallery = images_for(conn, [r["id"] for r in rows])
                    self.send_json({"products": [product_dict(r, r["category_name"] or "", gallery[r["id"]]) for r in rows]}); return
                if path == "/api/admin/categories":
                    if not self.require_admin(): return
                    with connect_db() as conn: rows = conn.execute("SELECT c.slug,c.name,c.sort_order,c.active,c.image,COUNT(p.id) AS product_count FROM categories c LEFT JOIN products p ON p.category=c.slug GROUP BY c.slug ORDER BY c.sort_order,c.name").fetchall()
                    self.send_json({"categories": [{**dict(r), "image": public_image(r["image"])} for r in rows]}); return
                if path == "/api/admin/orders":
                    if not self.require_admin(): return
                    qs = dict([part.split("=", 1) if "=" in part else (part, "") for part in parsed.query.split("&") if part])
                    status_filter = qs.get("status", "all")
                    with connect_db() as conn:
                        if status_filter != "all" and status_filter in ORDER_STATUSES:
                            rows = conn.execute("SELECT * FROM orders WHERE status=? ORDER BY id DESC LIMIT 300", (status_filter,)).fetchall()
                        else: rows = conn.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 300").fetchall()
                        orders = [order_dict(conn, r, True) for r in rows]
                    self.send_json({"orders": orders, "statuses": ORDER_STATUSES}); return
                if path == "/api/admin/settings":
                    if not self.require_admin(): return
                    with connect_db() as conn: settings = get_settings(conn)
                    self.send_json({"settings": settings}); return
                if path == "/api/admin/export/orders.csv":
                    if not self.require_admin(): return
                    self.export_orders(); return
                if path in ("/robots.txt", "/sitemap.xml"):
                    # Crawlers are kept out until the owner marks the store data as real and ready (Settings → site_ready).
                    with connect_db() as conn:
                        ready = get_settings(conn).get("site_ready", "0") == "1"
                        rows = conn.execute("SELECT p.slug,p.image,p.updated_at FROM products p JOIN categories c ON c.slug=p.category WHERE p.active=1 AND c.active=1 ORDER BY p.id DESC").fetchall() if ready else []
                    if path == "/robots.txt":
                        self._send(200, seo.robots_txt(self.base_url(), ready).encode(), "text/plain; charset=utf-8", {"Cache-Control": "public, max-age=3600"}); return
                    if not ready:
                        self._send(404, b"Not found", "text/plain; charset=utf-8"); return
                    products = [{"slug": r["slug"], "image": public_image(r["image"]), "updated_at": r["updated_at"]} for r in rows]
                    self._send(200, seo.sitemap_xml(self.base_url(), products, list(legal.PAGES)).encode(), "application/xml; charset=utf-8", {"Cache-Control": "public, max-age=3600"}); return
                if path.startswith("/api/"):
                    self.send_json({"error": "المسار غير موجود."}, 404); return
                if self.command in ("GET", "HEAD"):
                    self.serve_static(path); return
                self.send_json({"error": "الطريقة غير مدعومة."}, 405)
            except APIError as exc:
                self.send_json({"error": exc.message, **exc.extra}, exc.status, exc.headers)
            return


def backup_data(keep: int = 14) -> list[Path]:
    """Consistent copy of the database (SQLite online-backup API, safe while the server runs) + a tarball of uploads/.
    Files go to DATA_DIR/backups; only the newest `keep` of each kind are retained."""
    import tarfile
    target = DATA_DIR / "backups"
    target.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    db_copy = target / f"store-{stamp}.db"
    source = sqlite3.connect(DB_PATH)
    try:
        dest = sqlite3.connect(db_copy)
        with dest:
            source.backup(dest)
        dest.close()
    finally:
        source.close()
    tar_path = target / f"uploads-{stamp}.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(UPLOAD_DIR, arcname="uploads")
    for pattern in ("store-*.db", "uploads-*.tar.gz"):
        for old in sorted(target.glob(pattern))[:-max(1, keep)]:
            old.unlink(missing_ok=True)
    return [db_copy, tar_path]


def sweep_orphan_uploads(min_age: int = 24 * 3600) -> int:
    """Remove uploads that no product/category references (abandoned form, cancelled upload) and are older than a day."""
    removed = 0
    with connect_db() as conn:
        used = {row[0] for row in conn.execute("SELECT path FROM product_images UNION SELECT thumb FROM product_images UNION SELECT image FROM products UNION SELECT image FROM categories") if row[0]}
    used_names = {u.rsplit("/", 1)[-1] for u in used}
    for file in UPLOAD_DIR.iterdir():
        if file.is_file() and file.name not in used_names and file.name != ".gitkeep" and time.time() - file.stat().st_mtime > min_age:
            file.unlink(missing_ok=True)
            removed += 1
    return removed


def start_auto_backup(keep: int) -> None:
    def loop() -> None:
        while True:
            try:
                backup_data(keep)
                sweep_orphan_uploads()
            except Exception:
                traceback.print_exc()
            time.sleep(24 * 60 * 60)
    threading.Thread(target=loop, daemon=True, name="auto-backup").start()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "backup":
        init_db()
        for path in backup_data(int(os.environ.get("BACKUP_KEEP", "14"))):
            print(path)
        sys.exit(0)
    first_run_password = init_db()
    try:
        sweep_orphan_uploads()
    except Exception:
        traceback.print_exc()
    server = ThreadingHTTPServer((HOST, PORT), StoreHandler)
    server.daemon_threads = True
    if os.environ.get("AUTO_BACKUP", "") == "1":
        start_auto_backup(int(os.environ.get("BACKUP_KEEP", "14")))
    # SIGTERM (docker stop, systemd) finishes in-flight requests and exits cleanly.
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
    print(f"\nموقع المتجر: http://{HOST}:{PORT}/")
    print(f"لوحة الإدارة: http://{HOST}:{PORT}/admin")
    if first_run_password:
        # Shown once, never stored in plain text. Copy it now and change it from Settings → Security.
        print(f"أُنشئ حساب المدير لأول مرة — المستخدم: {ADMIN_USERNAME.strip() or 'admin'}")
        print(f"كلمة المرور المولّدة (تظهر هذه المرة فقط): {first_run_password}")
    if ALLOW_WEAK_PASSWORD:
        print("وضع التصميم المحلي: كلمة مرور قصيرة مسموحة على هذا الجهاز فقط. لن يعمل هذا الوضع عند النشر.")
    print("تنبيه: غيّر كلمة مرور المدير وفعّل HTTPS قبل أي نشر عام.\n", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
