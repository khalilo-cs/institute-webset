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
DB_PATH = BASE_DIR / "store.db"
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "4173"))
DEMO_ADMIN_USER = os.environ.get("ADMIN_USERNAME", "admin")
DEMO_ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "Fakhamah#2026Demo!")
SESSION_TTL = 12 * 60 * 60
MAX_BODY = 7 * 1024 * 1024
MAX_IMAGE_BYTES = 5 * 1024 * 1024

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


def init_db() -> None:
    with connect_db() as conn:
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
        admin = conn.execute("SELECT username FROM admins LIMIT 1").fetchone()
        if not admin:
            username = DEMO_ADMIN_USER.strip() or "admin"
            if len(DEMO_ADMIN_PASSWORD) < 12:
                raise RuntimeError("ADMIN_PASSWORD must be at least 12 characters")
            conn.execute("INSERT INTO admins(username,password_hash,updated_at) VALUES(?,?,?)",
                         (username, hash_password(DEMO_ADMIN_PASSWORD), now_iso()))


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


def product_dict(row: sqlite3.Row, category_name: str | None = None) -> dict:
    return {
        "id": row["id"], "name": row["name"], "slug": row["slug"], "category": row["category"],
        "category_name": category_name or "", "description": row["description"], "price": row["price"],
        "image": row["image"], "alt": row["alt"], "badge": row["badge"], "sku": row["sku"],
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


class StoreHandler(BaseHTTPRequestHandler):
    server_version = "FakhamahStore/1.0"

    def log_message(self, fmt: str, *args) -> None:
        # Keep access logs useful without dumping request bodies or credentials.
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, status: int, body: bytes, content_type: str = "application/json; charset=utf-8", headers: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        for key, value in (headers or {}).items():
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

    def serve_static(self, path: str) -> None:
        if path in ("/", "/index.html"):
            target = BASE_DIR / "index.html"
        elif path in ("/admin", "/admin/", "/admin.html"):
            target = BASE_DIR / "admin.html"
        elif path.startswith("/assets/") or path.startswith("/uploads/"):
            target = (BASE_DIR / path.lstrip("/")).resolve()
            if not target.is_relative_to(BASE_DIR.resolve()):
                self._send(404, b"Not found", "text/plain; charset=utf-8"); return
        else:
            self._send(404, b"Not found", "text/plain; charset=utf-8"); return
        if not target.is_file():
            self._send(404, b"Not found", "text/plain; charset=utf-8"); return
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in ("application/javascript", "application/json", "image/svg+xml"):
            content_type += "; charset=utf-8"
        try:
            body = target.read_bytes()
        except OSError:
            self._send(500, b"Unable to read file", "text/plain; charset=utf-8"); return
        headers = {"Last-Modified": formatdate(target.stat().st_mtime, usegmt=True)}
        if path.startswith("/assets/") or path.startswith("/uploads/"):
            headers["Cache-Control"] = "public, max-age=3600"
        else:
            headers["Cache-Control"] = "no-cache"
        self._send(200, body, content_type, headers)

    def handle_login(self, data: dict) -> None:
        username = str(data.get("username", "")).strip()[:100]
        password = str(data.get("password", ""))[:200]
        ip = self.client_address[0]
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
        token = secrets.token_urlsafe(36)
        csrf = secrets.token_urlsafe(24)
        SESSIONS[token] = {"username": admin["username"], "csrf": csrf, "expires": now + SESSION_TTL}
        cookie = f"fakhama_admin={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_TTL}"
        self.send_json({"ok": True, "username": admin["username"], "csrf": csrf}, 200, {"Set-Cookie": cookie})

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
                if image_value.startswith("/assets/") or image_value.startswith("/uploads/") or image_value.startswith("assets/"):
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
            self.send_json({"ok": True}, 200, {"Set-Cookie": "fakhama_admin=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"}); return
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
                        with connect_db() as conn: admin = conn.execute("SELECT username FROM admins LIMIT 1").fetchone()
                        self.send_json({"authenticated": True, "username": admin["username"], "csrf": session["csrf"]})
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
                if path == "/robots.txt":
                    host = self.headers.get("Host", "localhost")
                    body = f"User-agent: *\nAllow: /\nDisallow: /admin\nDisallow: /api/admin/\nSitemap: https://{host}/sitemap.xml\n".encode()
                    self._send(200, body, "text/plain; charset=utf-8"); return
                if path == "/sitemap.xml":
                    host = self.headers.get("Host", "localhost")
                    body = f"<?xml version=\"1.0\" encoding=\"UTF-8\"?><urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\"><url><loc>https://{host}/</loc><changefreq>daily</changefreq></url></urlset>".encode()
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
    init_db()
    server = ThreadingHTTPServer((HOST, PORT), StoreHandler)
    server.daemon_threads = True
    print(f"\nموقع المتجر: http://{HOST}:{PORT}/")
    print(f"لوحة الإدارة: http://{HOST}:{PORT}/admin")
    print(f"دخول العرض فقط — المستخدم: {DEMO_ADMIN_USER} | كلمة المرور: {DEMO_ADMIN_PASSWORD}")
    print("تنبيه: غيّر كلمة مرور المدير واضبط متغيرات البيئة قبل أي نشر عام.\n", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
