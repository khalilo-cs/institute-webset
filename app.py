#!/usr/bin/env python3
"""Small, dependency-free storefront + admin API backed by SQLite."""
from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import io
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import sys
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
SESSION_TTL = 12 * 60 * 60
MAX_BODY = 7 * 1024 * 1024
MAX_IMAGE_BYTES = 5 * 1024 * 1024
ORDER_LIMIT = (int(os.environ.get("ORDER_LIMIT_PER_10MIN", "10")), 600)  # public order attempts per client per 10 minutes
IMAGE_PATH_RE = re.compile(r"/?(?:assets|uploads)/[A-Za-z0-9][A-Za-z0-9._-]*")
HOST_RE = re.compile(r"[A-Za-z0-9.\-]+(?::\d{1,5})?|\[[0-9A-Fa-f:]+\](?::\d{1,5})?")
# Root-level files the server may serve (everything else is 404). Values are paths under BASE_DIR.
PUBLIC_FILES = {
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/admin-manifest.webmanifest": ("admin-manifest.webmanifest", "application/manifest+json"),
    "/sw.js": ("sw.js", "text/javascript"),
    "/offline.html": ("offline.html", "text/html"),
    "/favicon.ico": ("icons/favicon.ico", "image/x-icon"),
}
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
       "script-src 'self' 'unsafe-inline'; connect-src 'self'; manifest-src 'self'; worker-src 'self'; "
       "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")

CATEGORIES = [
    ("curtains", "ستائر تفصيل", 1),
    ("roller", "ستائر رول", 2),
    ("blinds", "ستائر شرائح معدنية", 3),
    ("electric", "ستائر كهربائية", 4),
    ("fabrics", "أقمشة الستائر", 5),
]
SEED_PRODUCTS = [
    ("ستائر بلاك أوت تفصيل", "curtains", "ستائر تعتيم تُفصّل حسب أبعاد النافذة. تختلف الخامة والسعر النهائي بحسب المقاس واختيار القماش.", 450, "assets/hero-curtains.jpg", "ستائر بلاك أوت وشيفون في غرفة معيشة أنيقة", "تفصيل حسب المقاس", "FAL-CUR-001", 20, 1),
    ("ستائر شيفون ناعمة", "curtains", "قماش شيفون يضيف إضاءة ناعمة وأناقة للنافذة. يُؤكد السعر النهائي بعد تحديد الأبعاد والخامة.", 350, "assets/fabrics.jpg", "قماش شيفون وستائر بلون عاجي", "خامة ناعمة", "FAL-CUR-002", 20, 1),
    ("ستارة رول بلاك أوت", "roller", "ستارة رول عملية للتحكم في الضوء والخصوصية، مع خيارات أقمشة وألوان متعددة بحسب التوفر.", 280, "assets/roller-curtains.jpg", "ستارة رول بلون رملي على نافذة عصرية", "رول تعتيم", "FAL-ROL-001", 15, 1),
    ("ستائر شرائح معدنية", "blinds", "شرائح معدنية تسمح بضبط اتجاه الضوء، وتُجهز بقياس النافذة بعد تأكيد المقاس النهائي.", 320, "assets/venetian-blinds.jpg", "ستائر شرائح معدنية على نافذة مضيئة", "تحكم بالضوء", "FAL-BLI-001", 12, 1),
    ("ستائر كهربائية بالتحكم", "electric", "نظام ستائر كهربائي للراحة اليومية. يحدد المتجر نوع المحرك وطريقة التركيب والتوافق بعد معاينة المقاس.", 1450, "assets/electric-curtains.jpg", "ستائر كهربائية طويلة في غرفة نوم هادئة", "تحكم مريح", "FAL-ELE-001", 6, 1),
    ("أقمشة ستائر — السعر للمتر", "fabrics", "تشكيلة أقمشة للستائر بدرجات وخامات متعددة. السعر المعروض تجريبي للمتر، ويُؤكد السعر والتوفر قبل الطلب.", 95, "assets/fabrics.jpg", "عينات أقمشة ستائر بدرجات ترابية", "خيارات أقمشة", "FAL-FAB-001", 40, 1),
    ("ستارة مزدوجة شيفون وبلاك أوت", "curtains", "تنسيق يجمع الشيفون مع قماش التعتيم للحصول على إضاءة وخصوصية مرنتين. السعر النهائي بعد تحديد المقاس.", 680, "assets/hero-curtains.jpg", "ستارة شيفون مع طبقة بلاك أوت في غرفة معيشة", "طبقتان أنيقتان", "FAL-CUR-003", 10, 1),
    ("ستارة رول نهاري وليلي", "roller", "شرائح متناوبة للتحكم بدرجة الضوء والخصوصية. اختر اللون والمقاس النهائي مع فريق المتجر.", 430, "assets/roller-curtains.jpg", "ستارة رول نهاري وليلي بلون محايد", "نهاري وليلي", "FAL-ROL-002", 9, 1),
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
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


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
        for slug, name, order in CATEGORIES:
            conn.execute("INSERT OR IGNORE INTO categories(slug,name,sort_order,active) VALUES(?,?,?,1)", (slug, name, order))
        existing = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        if existing == 0:
            ts = now_iso()
            for index, (name, category, description, price, image, alt, badge, sku, stock, featured) in enumerate(SEED_PRODUCTS, 1):
                slug = f"sample-{index}"
                conn.execute("""INSERT INTO products(name,slug,category,description,price,image,alt,badge,sku,stock,featured,active,created_at,updated_at)
                              VALUES(?,?,?,?,?,?,?,?,?,?,?,1,?,?)""",
                             (name, slug, category, description, price, image, alt, badge, sku, stock, featured, ts, ts))
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (key, value))
        # Earlier versions stored relative image paths ("assets/x.jpg") that break under /admin/.
        conn.execute("UPDATE products SET image='/'||image WHERE image LIKE 'assets/%' OR image LIKE 'uploads/%'")
        admin = conn.execute("SELECT username FROM admins LIMIT 1").fetchone()
        if not admin:
            username = ADMIN_USERNAME.strip() or "admin"
            password = ADMIN_PASSWORD
            if not password:
                password = generated_password = secrets.token_urlsafe(15)
            elif len(password) < 12:
                raise RuntimeError("ADMIN_PASSWORD must be at least 12 characters")
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


def public_image(value: str) -> str:
    """Images are always served from the site root, whichever page (/ or /admin/) requests them."""
    value = value or ""
    if value and not value.startswith(("/", "http://", "https://", "data:")):
        value = "/" + value
    return value


def product_dict(row: sqlite3.Row, category_name: str | None = None) -> dict:
    return {
        "id": row["id"], "name": row["name"], "slug": row["slug"], "category": row["category"],
        "category_name": category_name or "", "description": row["description"], "price": row["price"],
        "image": public_image(row["image"]), "alt": row["alt"], "badge": row["badge"], "sku": row["sku"],
        "stock": row["stock"], "featured": bool(row["featured"]), "active": bool(row["active"]),
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }


def order_dict(conn: sqlite3.Connection, row: sqlite3.Row, include_items: bool = True) -> dict:
    result = {key: row[key] for key in row.keys()}
    result["status_label"] = ORDER_STATUSES.get(row["status"], row["status"])
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
    server_version = "FakhamahStore/1.0"
    sys_version = ""

    def log_message(self, fmt: str, *args) -> None:
        # Keep access logs useful without dumping request bodies or credentials.
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

    def _send(self, status: int, body: bytes, content_type: str = "application/json; charset=utf-8", headers: dict | None = None) -> None:
        path = urlsplit(self.path).path
        out = {
            "Content-Type": content_type,
            "Content-Length": str(len(body)),
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "strict-origin-when-cross-origin",
            "Permissions-Policy": "geolocation=(), microphone=(), payment=(), usb=()",
            "Cache-Control": "no-store",
            "Connection": "close",
        }
        if content_type.startswith("text/html"):
            out["Content-Security-Policy"] = CSP
        if path.startswith(("/admin", "/api/admin")):
            out["X-Robots-Tag"] = "noindex, nofollow"
        if self.is_secure():
            out["Strict-Transport-Security"] = "max-age=15552000"
        out.update(headers or {})  # callers may override, e.g. Cache-Control for static files
        self.send_response(status)
        for key, value in out.items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_json(self, data, status: int = 200, headers: dict | None = None) -> None:
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8", headers)

    def read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise APIError("حجم الطلب غير صالح.", 400)
        if length > MAX_BODY:
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
        try:
            self.route()
        except APIError as exc:
            self.send_json({"error": exc.message}, exc.status)
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
        if not allow_attempt(ORDER_ATTEMPTS, self.client_ip(), *ORDER_LIMIT):
            raise APIError("تم إرسال طلبات كثيرة خلال وقت قصير. حاول بعد قليل أو تواصل مع المتجر هاتفيًا.", 429)
        data = self.read_json()
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
            cursor = conn.execute("""INSERT INTO orders(order_number,customer_name,phone,city,address,notes,status,subtotal,delivery_fee,total,created_at,updated_at)
                                      VALUES('PENDING',?,?,?,?,?,'new',?,?,?,?,?)""",
                                  (name, phone, city, address, notes, subtotal, delivery_fee, total, created, created))
            order_id = cursor.lastrowid
            order_number = f"MH-{datetime.now().strftime('%y%m%d')}-{order_id:05d}"
            conn.execute("UPDATE orders SET order_number=? WHERE id=?", (order_number, order_id))
            for row, quantity in products:
                conn.execute("INSERT INTO order_items(order_id,product_id,product_name,sku,unit_price,quantity) VALUES(?,?,?,?,?,?)",
                             (order_id, row["id"], row["name"], row["sku"], row["price"], quantity))
                conn.execute("UPDATE products SET stock=stock-?,updated_at=? WHERE id=?", (quantity, created, row["id"]))
        self.send_json({"ok": True, "order_id": order_id, "order_number": order_number, "subtotal": subtotal,
                        "delivery_fee": delivery_fee, "total": total}, 201)

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
        if path in ("/", "/index.html"):
            return BASE_DIR / "index.html", None
        if path in ("/admin", "/admin/", "/admin.html"):
            return BASE_DIR / "admin.html", None
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

    def serve_static(self, path: str) -> None:
        found = self.resolve_static(path)
        if not found or not found[0].is_file():
            self._send(404, b"Not found", "text/plain; charset=utf-8"); return
        target, forced_type = found
        content_type = forced_type or mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in ("application/javascript", "application/json", "application/manifest+json"):
            content_type += "; charset=utf-8"
        try:
            body = target.read_bytes()
        except OSError:
            self._send(500, b"Unable to read file", "text/plain; charset=utf-8"); return
        headers = {"Last-Modified": formatdate(target.stat().st_mtime, usegmt=True)}
        if path.startswith(("/assets/", "/uploads/", "/icons/")):
            headers["Cache-Control"] = "public, max-age=3600"
        else:
            headers["Cache-Control"] = "no-cache"
        if path == "/sw.js":
            headers["Service-Worker-Allowed"] = "/"
        self._send(200, body, content_type, headers)

    def handle_login(self, data: dict) -> None:
        username = str(data.get("username", "")).strip()[:100]
        password = str(data.get("password", ""))[:200]
        ip = self.client_ip()
        now = time.time()
        recent = [t for t in LOGIN_FAILURES.get(ip, []) if now - t < 300]
        if len(recent) >= 8:
            raise APIError("محاولات كثيرة. انتظر بضع دقائق ثم حاول مرة أخرى.", 429)
        with connect_db() as conn:
            admin = conn.execute("SELECT username,password_hash FROM admins WHERE username=?", (username,)).fetchone()
        if not admin or not verify_password(password, admin["password_hash"]):
            recent.append(now); LOGIN_FAILURES[ip] = recent
            raise APIError("اسم المستخدم أو كلمة المرور غير صحيحة.", 401)
        LOGIN_FAILURES.pop(ip, None)
        purge_expired(SESSIONS, 0, key_expires=True)
        token = secrets.token_urlsafe(36)
        csrf = secrets.token_urlsafe(24)
        SESSIONS[token] = {"username": admin["username"], "csrf": csrf, "expires": now + SESSION_TTL}
        self.send_json({"ok": True, "username": admin["username"], "csrf": csrf}, 200,
                       {"Set-Cookie": self.session_cookie(token, SESSION_TTL)})

    def session_cookie(self, value: str, max_age: int) -> str:
        secure = "; Secure" if self.is_secure() else ""
        return f"fakhama_admin={value}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}{secure}"

    def save_product(self, product_id: int | None, data: dict) -> None:
        name = str(data.get("name", "")).strip()[:140]
        category = str(data.get("category", "")).strip()[:100]
        description = str(data.get("description", "")).strip()[:4000]
        alt = str(data.get("alt", "")).strip()[:250]
        badge = str(data.get("badge", "")).strip()[:60]
        sku = str(data.get("sku", "")).strip()[:80]
        price = int_field(data.get("price"), "السعر", 1, 100_000_000)
        stock = int_field(data.get("stock", 0), "المخزون", 0, 1_000_000)
        if len(name) < 2: raise APIError("اسم المنتج مطلوب.")
        if not category: raise APIError("اختر قسمًا للمنتج.")
        if alt == "": alt = name
        with connect_db() as conn:
            cat = conn.execute("SELECT slug FROM categories WHERE slug=? AND active=1", (category,)).fetchone()
            if not cat: raise APIError("القسم المحدد غير موجود أو غير مفعّل.")
            old = conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone() if product_id else None
            if product_id and not old: raise APIError("المنتج غير موجود.", 404)
            image = old["image"] if old else ""
            if data.get("image_data"):
                image = save_image_data(data["image_data"])
            elif data.get("image"):
                image_value = str(data["image"]).strip()
                # Only an existing file under assets/ or uploads/ is accepted; anything else keeps the current image.
                if IMAGE_PATH_RE.fullmatch(image_value):
                    image_value = "/" + image_value.lstrip("/")
                    root = ASSETS_DIR if image_value.startswith("/assets/") else UPLOAD_DIR
                    if (root / image_value.split("/", 2)[2]).is_file():
                        image = image_value
            if not image: raise APIError("أضف صورة للمنتج.")
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
            row = conn.execute("SELECT p.*,c.name AS category_name FROM products p JOIN categories c ON c.slug=p.category WHERE p.id=?", (result_id,)).fetchone()
        self.send_json({"ok": True, "product": product_dict(row, row["category_name"])}, 200 if product_id else 201)

    def route_mutation(self) -> None:
        path = unquote(urlsplit(self.path).path)
        if path == "/api/admin/login" and self.command == "POST":
            self.handle_login(self.read_json()); return
        if path == "/api/admin/logout" and self.command == "POST":
            session = self.require_admin(write=True)
            if not session: return
            SESSIONS.pop(session["token"], None)
            self.send_json({"ok": True}, 200, {"Set-Cookie": self.session_cookie("", 0)}); return
        if path == "/api/admin/products" and self.command == "POST":
            if not self.require_admin(write=True): return
            self.save_product(None, self.read_json()); return
        match = re.fullmatch(r"/api/admin/products/(\d+)", path)
        if match and self.command == "PUT":
            if not self.require_admin(write=True): return
            self.save_product(int(match.group(1)), self.read_json()); return
        if match and self.command == "DELETE":
            if not self.require_admin(write=True): return
            product_id = int(match.group(1))
            with connect_db() as conn:
                cur = conn.execute("DELETE FROM products WHERE id=?", (product_id,))
                if not cur.rowcount: raise APIError("المنتج غير موجود.", 404)
            self.send_json({"ok": True}); return
        if path == "/api/admin/categories" and self.command == "POST":
            if not self.require_admin(write=True): return
            data = self.read_json(); name = str(data.get("name", "")).strip()[:80]
            slug = slugify(str(data.get("slug") or name))
            if len(name) < 2: raise APIError("اسم القسم مطلوب.")
            with connect_db() as conn:
                if conn.execute("SELECT 1 FROM categories WHERE slug=?", (slug,)).fetchone(): raise APIError("يوجد قسم بهذا الرابط بالفعل.", 409)
                sort = conn.execute("SELECT COALESCE(MAX(sort_order),0)+1 FROM categories").fetchone()[0]
                conn.execute("INSERT INTO categories(slug,name,sort_order,active) VALUES(?,?,?,1)", (slug, name, sort))
            self.send_json({"ok": True, "slug": slug, "name": name}, 201); return
        match = re.fullmatch(r"/api/admin/categories/([\w\-]+)", path, re.UNICODE)
        if match and self.command == "PUT":
            if not self.require_admin(write=True): return
            slug = match.group(1)
            data = self.read_json(); name = str(data.get("name", "")).strip()[:80]
            active = 1 if data.get("active", True) else 0
            if len(name) < 2: raise APIError("اسم القسم مطلوب.")
            with connect_db() as conn:
                cur = conn.execute("UPDATE categories SET name=?,active=? WHERE slug=?", (name, active, slug))
                if not cur.rowcount: raise APIError("القسم غير موجود.", 404)
            self.send_json({"ok": True, "slug": slug, "name": name, "active": bool(active)}); return
        if match and self.command == "DELETE":
            if not self.require_admin(write=True): return
            slug = match.group(1)
            with connect_db() as conn:
                count = conn.execute("SELECT COUNT(*) FROM products WHERE category=?", (slug,)).fetchone()[0]
                if count: raise APIError("انقل منتجات هذا القسم أو احذفها قبل حذف القسم.", 409)
                cur = conn.execute("DELETE FROM categories WHERE slug=?", (slug,))
                if not cur.rowcount: raise APIError("القسم غير موجود.", 404)
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
            updated = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
            result = order_dict(conn, updated, True)
        self.send_json({"ok": True, "order": result})

    def route(self) -> None:  # override dispatch to keep verb-specific mutations explicit
        parsed_path = urlsplit(self.path).path
        if self.command in ("POST", "PUT", "DELETE"):
            # Public order creation is the only unauthenticated write route.
            if parsed_path == "/api/orders" and self.command == "POST":
                try: self.create_order()
                except APIError as exc: self.send_json({"error": exc.message}, exc.status)
                return
            try: self.route_mutation()
            except APIError as exc: self.send_json({"error": exc.message}, exc.status)
            return
        if self.command == "PATCH":
            try: self.route_patch()
            except APIError as exc: self.send_json({"error": exc.message}, exc.status)
            return
        self.route_read_only()

    def route_read_only(self) -> None:
        # Reuse GET routes from route(), while preventing recursive dispatch.
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        if path in ("/api/health", "/api/store", "/api/categories", "/api/products", "/api/admin/me", "/api/admin/dashboard", "/api/admin/products", "/api/admin/categories", "/api/admin/orders", "/api/admin/settings", "/api/admin/export/orders.csv", "/robots.txt", "/sitemap.xml") or path.startswith("/"):
            # The GET/HEAD read router below duplicates the GET route table deliberately.
            try:
                if path == "/api/health": self.send_json({"ok": True, "service": "fakhama-store", "time": now_iso()}); return
                if path == "/api/store":
                    with connect_db() as conn: settings = get_settings(conn)
                    self.send_json({"name": settings["store_name"], "tagline": settings["tagline"], "city": settings["city"], "phone": settings["phone"], "whatsapp": settings["whatsapp"], "address": settings["address"], "map_url": settings.get("map_url", ""), "delivery_fee": int(settings.get("delivery_fee", "0") or 0), "delivery_note": settings.get("delivery_note", ""), "site_ready": settings.get("site_ready", "0") == "1", "seo_title": settings.get("seo_title", ""), "seo_description": settings.get("seo_description", ""), "hero_title": settings.get("hero_title", ""), "hero_copy": settings.get("hero_copy", "")}); return
                if path == "/api/categories":
                    with connect_db() as conn: rows = conn.execute("SELECT slug,name,sort_order FROM categories WHERE active=1 ORDER BY sort_order,name").fetchall()
                    self.send_json({"categories": [dict(r) for r in rows]}); return
                if path == "/api/products":
                    with connect_db() as conn: rows = conn.execute("SELECT p.*,c.name AS category_name FROM products p JOIN categories c ON c.slug=p.category WHERE p.active=1 AND c.active=1 ORDER BY p.featured DESC,p.id DESC").fetchall()
                    self.send_json({"products": [product_dict(r, r["category_name"]) for r in rows]}); return
                if path == "/api/admin/me":
                    session = self.require_admin()
                    if session:
                        self.send_json({"authenticated": True, "username": session["username"], "csrf": session["csrf"]})
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
                    self.send_json({"products": total_products, "active_products": active_products, "pending_orders": pending, "total_orders": total_orders, "delivered_revenue": delivered_revenue, "low_stock": low_stock, "recent_orders": recent}); return
                if path == "/api/admin/products":
                    if not self.require_admin(): return
                    with connect_db() as conn: rows = conn.execute("SELECT p.*,c.name AS category_name FROM products p LEFT JOIN categories c ON c.slug=p.category ORDER BY p.id DESC").fetchall()
                    self.send_json({"products": [product_dict(r, r["category_name"] or "") for r in rows]}); return
                if path == "/api/admin/categories":
                    if not self.require_admin(): return
                    with connect_db() as conn: rows = conn.execute("SELECT c.slug,c.name,c.sort_order,c.active,COUNT(p.id) AS product_count FROM categories c LEFT JOIN products p ON p.category=c.slug GROUP BY c.slug ORDER BY c.sort_order,c.name").fetchall()
                    self.send_json({"categories": [dict(r) for r in rows]}); return
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
                    with connect_db() as conn: ready = get_settings(conn).get("site_ready", "0") == "1"
                    if path == "/robots.txt":
                        if ready:
                            text = f"User-agent: *\nAllow: /\nDisallow: /admin\nDisallow: /api/\nSitemap: {self.base_url()}/sitemap.xml\n"
                        else:
                            text = "User-agent: *\nDisallow: /\n"
                        self._send(200, text.encode(), "text/plain; charset=utf-8"); return
                    if not ready:
                        self._send(404, b"Not found", "text/plain; charset=utf-8"); return
                    body = f"<?xml version=\"1.0\" encoding=\"UTF-8\"?><urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\"><url><loc>{self.base_url()}/</loc><changefreq>daily</changefreq></url></urlset>".encode()
                    self._send(200, body, "application/xml; charset=utf-8"); return
                if path.startswith("/api/"):
                    self.send_json({"error": "المسار غير موجود."}, 404); return
                if self.command in ("GET", "HEAD"):
                    self.serve_static(path); return
                self.send_json({"error": "الطريقة غير مدعومة."}, 405)
            except APIError as exc:
                self.send_json({"error": exc.message}, exc.status)
            return


if __name__ == "__main__":
    first_run_password = init_db()
    server = ThreadingHTTPServer((HOST, PORT), StoreHandler)
    server.daemon_threads = True
    print(f"\nموقع المتجر: http://{HOST}:{PORT}/")
    print(f"لوحة الإدارة: http://{HOST}:{PORT}/admin")
    if first_run_password:
        # Shown once, never stored in plain text. Copy it now and change it from Settings → Security.
        print(f"أُنشئ حساب المدير لأول مرة — المستخدم: {ADMIN_USERNAME.strip() or 'admin'}")
        print(f"كلمة المرور المولّدة (تظهر هذه المرة فقط): {first_run_password}")
    print("تنبيه: غيّر كلمة مرور المدير وفعّل HTTPS قبل أي نشر عام.\n", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
