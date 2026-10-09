"""Black-box tests for app.py. Run with:  python3 -m unittest discover -s tests -v

Each test class starts its own server process on a free port with a throw-away DATA_DIR, so nothing
touches a real store.db / uploads folder and the in-memory rate limiters never leak between classes.
"""
from __future__ import annotations

import base64
import http.client
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ["ENV_FILE"] = "off"  # a developer's local .env must never leak into test servers

# 1x1 PNG / smallest valid JPEG header + WebP container, enough for the server's magic-byte checks.
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
WEBP = b"RIFF" + b"\x24\x00\x00\x00" + b"WEBP" + b"VP8 " + b"\x00" * 32


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def data_uri(mime: str, raw: bytes) -> str:
    return f"data:{mime};base64," + base64.b64encode(raw).decode()


class ServerCase(unittest.TestCase):
    """Starts app.py as a subprocess. Subclasses may override ENV."""
    ENV: dict[str, str] = {}
    admin_password: str | None = None

    @classmethod
    def start_server(cls, data_dir: str | None = None, env: dict | None = None):
        cls.port = free_port()
        cls.data_dir = data_dir or tempfile.mkdtemp(prefix="fakhama-test-")
        full_env = {k: v for k, v in os.environ.items() if k not in ("ADMIN_PASSWORD", "ADMIN_USERNAME", "TRUST_PROXY", "PUBLIC_BASE_URL")}
        full_env.update({"PORT": str(cls.port), "HOST": "127.0.0.1", "DATA_DIR": cls.data_dir, "PYTHONUNBUFFERED": "1"})
        full_env.update(env or {})
        cls.proc = subprocess.Popen([sys.executable, "-I", str(ROOT / "app.py")], cwd=cls.data_dir, env=full_env,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", cls.port), timeout=0.3):
                    return
            except OSError:
                if cls.proc.poll() is not None:
                    raise RuntimeError("server exited early: " + cls.proc.stderr.read())
                time.sleep(0.1)
        raise RuntimeError("server did not start")

    @classmethod
    def stop_server(cls, remove_data: bool = True):
        cls.proc.terminate()
        try:
            cls.proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
            cls.proc.communicate()
        if remove_data:
            shutil.rmtree(cls.data_dir, ignore_errors=True)

    @classmethod
    def setUpClass(cls):
        cls.admin_password = "T" + secrets.token_urlsafe(18)
        cls.start_server(env={"ADMIN_PASSWORD": cls.admin_password, **cls.ENV})

    @classmethod
    def tearDownClass(cls):
        cls.stop_server()

    # -- helpers -----------------------------------------------------------------
    def req(self, method: str, path: str, body=None, headers: dict | None = None, cookie: str | None = None, csrf: str | None = None):
        """Raw request: the path is sent exactly as given (no client-side '..' normalisation)."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        hdrs = dict(headers or {})
        payload = None
        if body is not None:
            payload = body if isinstance(body, bytes) else json.dumps(body).encode()
            hdrs.setdefault("Content-Type", "application/json")
        if cookie:
            hdrs["Cookie"] = cookie
        if csrf:
            hdrs["X-CSRF-Token"] = csrf
        conn.request(method, path, body=payload, headers=hdrs)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, resp, data

    def json(self, method, path, body=None, **kw):
        status, resp, data = self.req(method, path, body, **kw)
        try:
            parsed = json.loads(data.decode()) if data else {}
        except json.JSONDecodeError:
            parsed = {"_raw": data[:200]}
        return status, parsed, resp

    def login(self, password: str | None = None, username: str = "admin"):
        status, data, resp = self.json("POST", "/api/admin/login", {"username": username, "password": password or self.admin_password})
        self.assertEqual(status, 200, data)
        cookie = resp.getheader("Set-Cookie").split(";")[0]
        return cookie, data["csrf"]


class PublicAndStaticTests(ServerCase):
    def test_health_store_categories_products(self):
        self.assertEqual(self.json("GET", "/api/health")[0], 200)
        status, store, _ = self.json("GET", "/api/store")
        self.assertEqual(status, 200)
        self.assertEqual(store["name"], "الفخامة للأقمشة والستائر")
        self.assertIn("مشرفة", store["address"])
        self.assertEqual(store["phone"], "+966 57 648 6491")
        self.assertEqual(store["whatsapp"], "", "WhatsApp number must stay empty until confirmed")
        self.assertEqual(store["map_url"], "https://maps.app.goo.gl/kn2kxuPujQT9raLG7?g_st=awb")
        _, cats, _ = self.json("GET", "/api/categories")
        self.assertEqual(len(cats["categories"]), 5)
        _, prods, _ = self.json("GET", "/api/products")
        self.assertEqual(len(prods["products"]), 18)  # the owner's 18 curtain photos; no invented sample products
        for p in prods["products"]:
            self.assertTrue(p["image"].startswith("/"), f"image must be root-relative: {p['image']}")
            status, _, _ = self.req("GET", p["image"])
            self.assertEqual(status, 200, p["image"])

    def test_pages_and_assets(self):
        for path in ("/", "/index.html", "/admin", "/admin/", "/assets/wavy-11.jpg"):
            status, resp, _ = self.req("GET", path)
            self.assertEqual(status, 200, path)
        status, resp, body = self.req("GET", "/")
        self.assertIn("text/html", resp.getheader("Content-Type"))
        self.assertIn(b'rel="manifest"', body)
        self.assertIn(b'href="/admin/"', body, "storefront footer links to the admin login (same app, same database)")
        self.assertEqual(self.req("HEAD", "/")[0], 200)

    def test_path_traversal_is_blocked(self):
        # The original build served store.db / app.py for these.
        attacks = [
            "/assets/../store.db", "/assets/../app.py", "/assets/%2e%2e/app.py", "/assets/%2e%2e%2fstore.db",
            "/assets/..%2fstore.db", "/uploads/../store.db", "/uploads/%2e%2e/app.py", "/icons/../app.py",
            "/assets/..\\app.py", "/assets/%5c..%5capp.py", "/assets/%00.jpg", "//assets/../store.db",
            "/assets/./../store.db", "/assets/", "/uploads/",
        ]
        for path in attacks:
            status, _, body = self.req("GET", path)
            self.assertEqual(status, 404, path)
            self.assertNotIn(b"SQLite format", body, path)
            self.assertNotIn(b"hash_password", body, path)

    def test_non_public_files_are_not_served(self):
        for path in ("/app.py", "/store.db", "/DATABASE.md", "/README.md", "/tests/test_app.py", "/.git/config", "/.env", "/android/build.gradle"):
            self.assertEqual(self.req("GET", path)[0], 404, path)

    def test_security_headers(self):
        _, resp, _ = self.req("GET", "/")
        self.assertEqual(resp.getheader("X-Content-Type-Options"), "nosniff")
        self.assertEqual(resp.getheader("X-Frame-Options"), "DENY")
        self.assertIn("frame-ancestors 'none'", resp.getheader("Content-Security-Policy"))
        self.assertEqual([k for k, _ in resp.getheaders()].count("Cache-Control"), 1, "single Cache-Control header")
        self.assertNotIn("Python", resp.getheader("Server") or "")
        _, resp, _ = self.req("GET", "/admin")
        self.assertIn("noindex", resp.getheader("X-Robots-Tag") or "")
        self.assertNotIn("Strict-Transport-Security", dict(resp.getheaders()), "no HSTS over plain HTTP")

    def test_pwa_files(self):
        status, resp, body = self.req("GET", "/manifest.webmanifest")
        self.assertEqual(status, 200)
        self.assertIn("manifest+json", resp.getheader("Content-Type"))
        customer = json.loads(body)
        status, resp, body = self.req("GET", "/admin-manifest.webmanifest")
        self.assertEqual(status, 200)
        admin = json.loads(body)
        self.assertEqual(customer["scope"], "/")
        self.assertEqual(admin["scope"], "/admin/")
        self.assertEqual(admin["start_url"], "/admin/")
        self.assertNotEqual(customer["id"], admin["id"])
        for manifest in (customer, admin):
            for icon in manifest["icons"]:
                self.assertEqual(self.req("GET", icon["src"])[0], 200, icon["src"])
        status, resp, body = self.req("GET", "/sw.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", resp.getheader("Content-Type"))
        self.assertEqual(resp.getheader("Service-Worker-Allowed"), "/")
        self.assertEqual(self.req("GET", "/offline.html")[0], 200)

    def test_service_worker_never_touches_api_admin_or_writes(self):
        _, _, body = self.req("GET", "/sw.js")
        src = body.decode()
        self.assertIn("/api/", src)
        self.assertIn("/admin", src)
        self.assertRegex(src, r"request\.method\s*!==\s*['\"]GET['\"]")
        self.assertNotIn("localStorage", src)

    def test_robots_and_sitemap_follow_site_ready(self):
        _, _, robots = self.req("GET", "/robots.txt")
        self.assertIn(b"Disallow: /\n", robots)
        self.assertEqual(self.req("GET", "/sitemap.xml")[0], 404)
        cookie, csrf = self.login()
        status, data, _ = self.json("PUT", "/api/admin/settings", {"site_ready": True}, cookie=cookie, csrf=csrf)
        self.assertEqual(status, 200, data)
        try:
            _, _, robots = self.req("GET", "/robots.txt", headers={"Host": "shop.example.com"})
            self.assertIn(b"Allow: /", robots)
            self.assertIn(b"Sitemap: http://shop.example.com/sitemap.xml", robots)
            status, _, sitemap = self.req("GET", "/sitemap.xml", headers={"Host": "shop.example.com"})
            self.assertEqual(status, 200)
            self.assertIn(b"<loc>http://shop.example.com/</loc>", sitemap)
            # A hostile Host header must not be reflected into the sitemap.
            _, _, robots = self.req("GET", "/robots.txt", headers={"Host": "evil.com/<script>"})
            self.assertNotIn(b"evil.com", robots)
        finally:
            self.json("PUT", "/api/admin/settings", {"site_ready": False}, cookie=cookie, csrf=csrf)


class AdminTests(ServerCase):
    def test_admin_endpoints_require_login(self):
        for path in ("/api/admin/me", "/api/admin/dashboard", "/api/admin/products", "/api/admin/categories",
                     "/api/admin/orders", "/api/admin/settings", "/api/admin/export/orders.csv", "/api/admin/messages"):
            self.assertEqual(self.req("GET", path)[0], 401, path)
        self.assertEqual(self.json("POST", "/api/admin/products", {"name": "x"})[0], 401)

    def test_login_cookie_flags_and_csrf(self):
        status, data, _ = self.json("POST", "/api/admin/login", {"username": "admin", "password": "wrong-password-123"})
        self.assertEqual(status, 401)
        status, data, resp = self.json("POST", "/api/admin/login", {"username": "admin", "password": self.admin_password})
        self.assertEqual(status, 200)
        cookie_header = resp.getheader("Set-Cookie")
        self.assertIn("HttpOnly", cookie_header)
        self.assertIn("SameSite=Lax", cookie_header)
        self.assertNotIn("Secure", cookie_header, "Secure only when served over HTTPS behind TRUST_PROXY")
        cookie = cookie_header.split(";")[0]
        status, me, _ = self.json("GET", "/api/admin/me", cookie=cookie)
        self.assertEqual((status, me["username"]), (200, "admin"))
        # Writes need the CSRF token.
        self.assertEqual(self.json("POST", "/api/admin/categories", {"name": "قسم اختبار"}, cookie=cookie)[0], 403)
        self.assertEqual(self.json("POST", "/api/admin/categories", {"name": "قسم اختبار"}, cookie=cookie, csrf="bad")[0], 403)
        # Logout invalidates the session.
        self.assertEqual(self.json("POST", "/api/admin/logout", {}, cookie=cookie, csrf=data["csrf"])[0], 200)
        self.assertEqual(self.req("GET", "/api/admin/me", cookie=cookie)[0], 401)

    def test_product_crud_with_image_upload_and_validation(self):
        cookie, csrf = self.login()
        kw = dict(cookie=cookie, csrf=csrf)
        base = {"name": "منتج اختبار", "category": "fabrics", "price": 120, "stock": 5, "description": "وصف", "sku": "T-1"}
        # No image -> rejected.
        self.assertEqual(self.json("POST", "/api/admin/products", base, **kw)[0], 400)
        # Wrong declared type, mismatching magic bytes, oversized, bad base64.
        bad = [
            data_uri("image/gif", b"GIF89a" + b"\0" * 20),
            data_uri("image/png", JPEG),
            data_uri("image/jpeg", PNG),
            data_uri("image/webp", PNG),
            "data:image/png;base64,@@@not-base64@@@",
            "not a data uri",
            data_uri("image/png", PNG + b"\0" * (5 * 1024 * 1024)),
            data_uri("image/svg+xml", b"<svg onload=alert(1)/>"),
        ]
        for uri in bad:
            status, data, _ = self.json("POST", "/api/admin/products", {**base, "image_data": uri}, **kw)
            self.assertIn(status, (400, 413), (uri[:40], data))
        # Good uploads (png/jpeg/webp) are stored under /uploads/ with generated names.
        created = []
        for mime, raw in (("image/png", PNG), ("image/jpeg", JPEG), ("image/webp", WEBP)):
            status, data, _ = self.json("POST", "/api/admin/products", {**base, "name": f"منتج {mime}", "image_data": data_uri(mime, raw)}, **kw)
            self.assertEqual(status, 201, data)
            image = data["product"]["image"]
            self.assertRegex(image, r"^/uploads/[0-9a-f]{32}\.(png|jpg|webp)$")
            status, resp, body = self.req("GET", image)
            self.assertEqual(status, 200)
            self.assertEqual(body, raw)
            self.assertEqual(resp.getheader("X-Content-Type-Options"), "nosniff")
            created.append(data["product"])
        product = created[0]
        # Invalid fields.
        for patch in ({"price": "abc"}, {"price": -5}, {"stock": -1}, {"name": "x"}, {"category": "nope"}, {"category": ""}):
            status, data, _ = self.json("PUT", f"/api/admin/products/{product['id']}", {**base, "image": product["image"], **patch}, **kw)
            self.assertEqual(status, 400, (patch, data))
        # Update keeps the existing image; a path outside assets/uploads is ignored, not stored.
        status, data, _ = self.json("PUT", f"/api/admin/products/{product['id']}",
                                    {**base, "name": "اسم جديد", "price": 200, "image": "/assets/../store.db"}, **kw)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["product"]["image"], product["image"])
        self.assertEqual((data["product"]["name"], data["product"]["price"]), ("اسم جديد", 200))
        # Switching to a bundled asset (with or without the leading slash) works.
        for image in ("/assets/wavy-02.jpg", "assets/wavy-02.jpg"):
            status, data, _ = self.json("PUT", f"/api/admin/products/{product['id']}", {**base, "image": image}, **kw)
            self.assertEqual((status, data["product"]["image"]), (200, "/assets/wavy-02.jpg"))
        # Hidden products disappear from the storefront and reappear when activated.
        self.json("PUT", f"/api/admin/products/{product['id']}", {**base, "image": "/assets/wavy-02.jpg", "active": False}, **kw)
        ids = [p["id"] for p in self.json("GET", "/api/products")[1]["products"]]
        self.assertNotIn(product["id"], ids)
        self.json("PUT", f"/api/admin/products/{product['id']}", {**base, "image": "/assets/wavy-02.jpg", "active": True}, **kw)
        ids = [p["id"] for p in self.json("GET", "/api/products")[1]["products"]]
        self.assertIn(product["id"], ids)
        # Delete.
        for p in created:
            self.assertEqual(self.json("DELETE", f"/api/admin/products/{p['id']}", **kw)[0], 200)
        self.assertEqual(self.json("DELETE", f"/api/admin/products/{created[0]['id']}", **kw)[0], 404)

    def test_price_zero_means_price_on_request(self):
        cookie, csrf = self.login()
        kw = dict(cookie=cookie, csrf=csrf)
        body = {"name": "ستارة حسب الطلب", "category": "curtains", "price": 0, "stock": 5, "image": "/assets/wavy-01.jpg"}
        status, data, _ = self.json("POST", "/api/admin/products", body, **kw)
        self.assertEqual((status, data["product"]["price"]), (201, 0), data)
        pid = data["product"]["id"]
        # An order that mixes a priced and a price-on-request line: the server totals only the priced one and flags the order.
        status, made, _ = self.json("POST", "/api/admin/products", {"name": "ستارة بسعر ثابت", "category": "curtains", "price": 300, "stock": 5, "image": "/assets/wavy-02.jpg"}, **kw)
        self.assertEqual(status, 201, made)
        priced = made["product"]
        status, order, _ = self.json("POST", "/api/orders", {"name": "عميل اختبار", "phone": "0551234567", "city": "جدة", "consent": True,
                                                             "items": [{"product_id": pid, "quantity": 2}, {"product_id": priced["id"], "quantity": 1}]})
        self.assertEqual(status, 201, order)
        self.assertEqual(order["subtotal"], priced["price"])
        _, orders, _ = self.json("GET", "/api/admin/orders", **kw)
        mine = next(o for o in orders["orders"] if o["id"] == order["order_id"])
        self.assertTrue(mine["on_request"])
        _, dash, _ = self.json("GET", "/api/admin/dashboard", **kw)
        self.assertTrue(any(o["on_request"] for o in dash["recent_orders"] if o["id"] == order["order_id"]))
        self.json("PATCH", f"/api/admin/orders/{mine['id']}", {"status": "cancelled"}, **kw)
        self.json("DELETE", f"/api/admin/products/{pid}", **kw)
        self.json("DELETE", f"/api/admin/products/{priced['id']}", **kw)

    def test_category_lifecycle(self):
        cookie, csrf = self.login()
        kw = dict(cookie=cookie, csrf=csrf)
        status, data, _ = self.json("POST", "/api/admin/categories", {"name": "المجالس العربية"}, **kw)
        self.assertEqual(status, 201, data)
        slug = data["slug"]
        self.assertEqual(self.json("POST", "/api/admin/categories", {"name": "المجالس العربية"}, **kw)[0], 409)
        self.assertEqual(self.json("POST", "/api/admin/categories", {"name": "x"}, **kw)[0], 400)
        from urllib.parse import quote
        url = f"/api/admin/categories/{quote(slug)}"
        self.assertEqual(self.json("PUT", url, {"name": "مجالس", "active": False}, **kw)[0], 200)
        self.assertNotIn(slug, [c["slug"] for c in self.json("GET", "/api/categories")[1]["categories"]])
        self.assertEqual(self.json("PUT", url, {"name": "مجالس", "active": True}, **kw)[0], 200)
        self.assertIn(slug, [c["slug"] for c in self.json("GET", "/api/categories")[1]["categories"]])
        # A category with products cannot be deleted.
        status, prod, _ = self.json("POST", "/api/admin/products", {"name": "منتج قسم", "category": slug, "price": 10, "stock": 1, "image": "/assets/wavy-02.jpg"}, **kw)
        self.assertEqual(status, 201, prod)
        self.assertEqual(self.json("DELETE", url, **kw)[0], 409)
        self.json("DELETE", f"/api/admin/products/{prod['product']['id']}", **kw)
        self.assertEqual(self.json("DELETE", url, **kw)[0], 200)
        self.assertEqual(self.json("DELETE", url, **kw)[0], 404)

    def test_settings_validation_and_persistence(self):
        cookie, csrf = self.login()
        kw = dict(cookie=cookie, csrf=csrf)
        for bad in ("javascript:alert(1)", "data:text/html,x", "ftp://x.y", "not a url"):
            self.assertEqual(self.json("PUT", "/api/admin/settings", {"map_url": bad}, **kw)[0], 400, bad)
            self.assertEqual(self.json("PUT", "/api/admin/settings", {"instagram": bad}, **kw)[0], 400, bad)
        self.assertEqual(self.json("PUT", "/api/admin/settings", {"delivery_fee": -1}, **kw)[0], 400)
        self.assertEqual(self.json("PUT", "/api/admin/settings", {"delivery_fee": "x"}, **kw)[0], 400)
        status, data, _ = self.json("PUT", "/api/admin/settings", {"delivery_fee": 25, "tagline": "عبارة جديدة"}, **kw)
        self.assertEqual(status, 200, data)
        _, store, _ = self.json("GET", "/api/store")
        self.assertEqual((store["delivery_fee"], store["tagline"]), (25, "عبارة جديدة"))
        # Address / map / phone stay untouched unless the admin changes them.
        self.assertEqual(store["map_url"], "https://maps.app.goo.gl/kn2kxuPujQT9raLG7?g_st=awb")
        self.assertEqual(store["phone"], "+966 57 648 6491")
        self.json("PUT", "/api/admin/settings", {"delivery_fee": 0}, **kw)

    def test_password_change(self):
        cookie, csrf = self.login()
        other_cookie, _ = self.login()  # a second session that must die when the password changes
        kw = dict(cookie=cookie, csrf=csrf)
        new = "N" + secrets.token_urlsafe(18)
        self.assertEqual(self.json("POST", "/api/admin/password", {"current_password": self.admin_password, "new_password": "short"}, **kw)[0], 400)
        self.assertEqual(self.json("POST", "/api/admin/password", {"current_password": "wrong-wrong-wrong", "new_password": new}, **kw)[0], 403)
        self.assertEqual(self.json("POST", "/api/admin/password", {"current_password": self.admin_password, "new_password": new}, **kw)[0], 200)
        self.assertEqual(self.req("GET", "/api/admin/me", cookie=cookie)[0], 200, "current session survives")
        self.assertEqual(self.req("GET", "/api/admin/me", cookie=other_cookie)[0], 401, "other sessions are revoked")
        self.assertEqual(self.json("POST", "/api/admin/login", {"username": "admin", "password": self.admin_password})[0], 401)
        cookie2, csrf2 = self.login(new)
        # restore for other tests in this class
        self.assertEqual(self.json("POST", "/api/admin/password", {"current_password": new, "new_password": self.admin_password}, cookie=cookie2, csrf=csrf2)[0], 200)


class OrderTests(ServerCase):
    ENV = {"ORDER_LIMIT_PER_10MIN": "1000"}  # these tests place many orders; the limiter has its own test below

    def make_product(self, price=250, stock=10, name="ستارة اختبار الطلبات"):
        cookie, csrf = self.login()
        status, data, _ = self.json("POST", "/api/admin/products",
                                    {"name": name, "category": "curtains", "price": price, "stock": stock, "sku": "T-ORDER", "image": "/assets/wavy-03.jpg"},
                                    cookie=cookie, csrf=csrf)
        self.assertEqual(status, 201, data)
        return data["product"]

    def stock_of(self, product_id):
        return next(p["stock"] for p in self.json("GET", "/api/products")[1]["products"] if p["id"] == product_id)

    def order(self, items, **fields):
        body = {"name": "عميل اختبار", "phone": "0551234567", "city": "جدة", "consent": True, "note": "ملاحظة", "items": items, **fields}
        return self.json("POST", "/api/orders", body)

    def test_order_flow_totals_stock_and_cancel(self):
        cookie, csrf = self.login()
        kw = dict(cookie=cookie, csrf=csrf)
        self.json("PUT", "/api/admin/settings", {"delivery_fee": 30}, **kw)
        p = self.make_product(price=280, stock=15)
        # The client-supplied price/total is ignored; the server prices from the DB.
        status, data, _ = self.order([{"product_id": p["id"], "quantity": 2, "price": 1}], total=1, subtotal=1)
        self.assertEqual(status, 201, data)
        self.assertEqual((data["subtotal"], data["delivery_fee"], data["total"]), (560, 30, 590))
        self.assertRegex(data["order_number"], r"^MH-\d{6}-\d{5}$")
        self.assertEqual(self.stock_of(p["id"]), 13)
        _, orders, _ = self.json("GET", "/api/admin/orders", **kw)
        order = next(o for o in orders["orders"] if o["id"] == data["order_id"])
        self.assertEqual((order["status"], len(order["items"]), order["items"][0]["quantity"], order["on_request"]), ("new", 1, 2, False))
        # Cancel returns the stock; re-activating takes it again.
        self.assertEqual(self.json("PATCH", f"/api/admin/orders/{order['id']}", {"status": "cancelled"}, **kw)[0], 200)
        self.assertEqual(self.stock_of(p["id"]), 15)
        self.assertEqual(self.json("PATCH", f"/api/admin/orders/{order['id']}", {"status": "confirmed"}, **kw)[0], 200)
        self.assertEqual(self.stock_of(p["id"]), 13)
        self.assertEqual(self.json("PATCH", f"/api/admin/orders/{order['id']}", {"status": "bogus"}, **kw)[0], 400)
        # Dashboard + CSV.
        _, dash, _ = self.json("GET", "/api/admin/dashboard", **kw)
        self.assertGreaterEqual(dash["total_orders"], 1)
        status, resp, body = self.req("GET", "/api/admin/export/orders.csv", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIn("text/csv", resp.getheader("Content-Type"))
        self.assertTrue(body.startswith(b"\xef\xbb\xbf"))
        self.assertIn(data["order_number"].encode(), body)
        self.json("PUT", "/api/admin/settings", {"delivery_fee": 0}, **kw)

    def test_order_validation(self):
        p = self.make_product(price=95, stock=40)
        self.assertEqual(self.order([{"product_id": p["id"], "quantity": p["stock"] + 1}])[0], 409)
        self.assertEqual(self.order([{"product_id": p["id"], "quantity": 0}])[0], 400)
        self.assertEqual(self.order([{"product_id": p["id"], "quantity": 51}])[0], 400)
        self.assertEqual(self.order([{"product_id": None, "quantity": 1}])[0], 400, "NaN ids from the old sample fallback")
        self.assertEqual(self.order([{"product_id": "curtain-blackout", "quantity": 1}])[0], 400)
        self.assertEqual(self.order([{"product_id": 999999, "quantity": 1}])[0], 409)
        self.assertEqual(self.order([])[0], 400)
        self.assertEqual(self.order("nope")[0], 400)
        self.assertEqual(self.order([{"product_id": p["id"], "quantity": 1}], name="x")[0], 400)
        self.assertEqual(self.order([{"product_id": p["id"], "quantity": 1}], phone="12")[0], 400)
        self.assertEqual(self.order([{"product_id": p["id"], "quantity": 1}], city="")[0], 400)
        self.assertEqual(self.req("POST", "/api/orders", b"{not json")[0], 400)
        self.assertEqual(self.req("POST", "/api/orders", b"[1,2]")[0], 400)
        self.assertEqual(self.stock_of(p["id"]), 40, "failed orders must not change stock")

    def test_hidden_product_cannot_be_ordered(self):
        p = self.make_product()
        cookie, csrf = self.login()
        self.json("PUT", f"/api/admin/products/{p['id']}", {"name": p["name"], "category": "curtains", "price": 250, "stock": 10, "image": "/assets/wavy-03.jpg", "active": False}, cookie=cookie, csrf=csrf)
        self.assertEqual(self.order([{"product_id": p["id"], "quantity": 1}])[0], 409)

    def test_xss_payload_is_stored_verbatim_and_returned_as_json(self):
        p = self.make_product()
        payload = "<img src=x onerror=alert(1)>"
        status, data, _ = self.order([{"product_id": p["id"], "quantity": 1}], name=payload + " عميل")
        self.assertEqual(status, 201)
        cookie, csrf = self.login()
        _, resp, body = self.req("GET", "/api/admin/orders", cookie=cookie)
        self.assertIn("application/json", resp.getheader("Content-Type"))
        self.assertIn("onerror", body.decode())  # escaping happens in the admin UI (esc()), data stays intact


class RateLimitTests(ServerCase):
    def test_login_lockout_after_repeated_failures(self):
        for _ in range(8):
            self.assertEqual(self.json("POST", "/api/admin/login", {"username": "admin", "password": "nope-nope-nope"})[0], 401)
        self.assertEqual(self.json("POST", "/api/admin/login", {"username": "admin", "password": "nope-nope-nope"})[0], 429)
        self.assertEqual(self.json("POST", "/api/admin/login", {"username": "admin", "password": self.admin_password})[0], 429)
        _, _, resp = self.json("POST", "/api/admin/login", {"username": "admin", "password": "nope-nope-nope"})
        self.assertEqual(resp.getheader("Retry-After"), "300")


class OrderRateLimitTests(ServerCase):
    def test_public_order_spam_is_limited(self):
        p = next(p for p in self.json("GET", "/api/products")[1]["products"] if p["stock"] >= 20)
        codes = []
        for _ in range(12):
            codes.append(self.json("POST", "/api/orders", {"name": "عميل", "phone": "0551234567", "city": "جدة", "consent": True,
                                                           "items": [{"product_id": p["id"], "quantity": 1}]})[0])
        self.assertEqual(codes[:10], [201] * 10)
        self.assertEqual(codes[10:], [429, 429])


class ProxyModeTests(ServerCase):
    ENV = {"TRUST_PROXY": "1", "PUBLIC_BASE_URL": "https://shop.example.com"}

    def test_secure_cookie_hsts_and_forwarded_ip(self):
        status, data, resp = self.json("POST", "/api/admin/login", {"username": "admin", "password": self.admin_password},
                                       headers={"X-Forwarded-Proto": "https", "X-Forwarded-For": "203.0.113.9"})
        self.assertEqual(status, 200)
        self.assertIn("Secure", resp.getheader("Set-Cookie"))
        self.assertIn("max-age", resp.getheader("Strict-Transport-Security"))
        # Lockout is per forwarded client, not per proxy: one client failing does not lock out another.
        for _ in range(8):
            self.json("POST", "/api/admin/login", {"username": "admin", "password": "nope-nope-nope"}, headers={"X-Forwarded-For": "198.51.100.1"})
        self.assertEqual(self.json("POST", "/api/admin/login", {"username": "admin", "password": "nope-nope-nope"}, headers={"X-Forwarded-For": "198.51.100.1"})[0], 429)
        self.assertEqual(self.json("POST", "/api/admin/login", {"username": "admin", "password": self.admin_password}, headers={"X-Forwarded-For": "198.51.100.2"})[0], 200)
        # Forged leading entries are ignored (right-most wins).
        self.assertEqual(self.json("POST", "/api/admin/login", {"username": "admin", "password": "nope-nope-nope"},
                                   headers={"X-Forwarded-For": "198.51.100.2, 198.51.100.1"})[0], 429)

    def test_public_base_url_used_for_robots(self):
        cookie, csrf = self.login()
        self.json("PUT", "/api/admin/settings", {"site_ready": True}, cookie=cookie, csrf=csrf)
        _, _, robots = self.req("GET", "/robots.txt", headers={"Host": "attacker.test"})
        self.assertIn(b"Sitemap: https://shop.example.com/sitemap.xml", robots)


class FirstRunPasswordTests(ServerCase):
    @classmethod
    def setUpClass(cls):
        pass  # each test manages its own server

    @classmethod
    def tearDownClass(cls):
        pass

    def test_generated_password_is_printed_once_and_works(self):
        self.__class__.start_server()
        try:
            time.sleep(0.5)
            self.__class__.proc.terminate()
            out, _ = self.__class__.proc.communicate(timeout=5)
            match = re.search(r"كلمة المرور المولّدة \(تظهر هذه المرة فقط\): (\S+)", out)
            self.assertIsNotNone(match, out)
            password = match.group(1)
            self.assertGreaterEqual(len(password), 16)
            self.assertNotIn("Fakhamah#2026Demo!", out)
            # Restart on the same data dir: the password is not printed again and still works.
            data_dir = self.__class__.data_dir
            self.__class__.start_server(data_dir=data_dir)
            self.admin_password = password
            self.login()
            self.__class__.proc.terminate()
            out2, _ = self.__class__.proc.communicate(timeout=5)
            self.assertNotIn("كلمة المرور المولّدة", out2)
            self.assertNotIn(password, out2)
        finally:
            self.__class__.stop_server()

    def test_short_env_password_is_rejected(self):
        port = free_port()
        data_dir = tempfile.mkdtemp(prefix="fakhama-test-")
        try:
            proc = subprocess.run([sys.executable, "-I", str(ROOT / "app.py")], cwd=data_dir, capture_output=True, text=True, timeout=20,
                                  env={**os.environ, "PORT": str(port), "DATA_DIR": data_dir, "ADMIN_PASSWORD": "short"})
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("at least 12", proc.stderr)
        finally:
            shutil.rmtree(data_dir, ignore_errors=True)

    def run_app(self, data_dir: str, **extra) -> subprocess.CompletedProcess:
        env = {**os.environ, "PORT": str(free_port()), "DATA_DIR": data_dir, **extra}
        return subprocess.run([sys.executable, "-I", str(ROOT / "app.py")], cwd=data_dir, capture_output=True, text=True, timeout=3, env=env)

    def test_weak_password_needs_local_dev_switch(self):
        data_dir = tempfile.mkdtemp(prefix="fakhama-test-")
        try:
            # Without the switch a 6-character password is refused (covered above); with it, a loopback server starts.
            with self.assertRaises(subprocess.TimeoutExpired):
                self.run_app(data_dir, ADMIN_PASSWORD="123456", ALLOW_WEAK_PASSWORD="1")
            # The switch is refused as soon as the server is reachable from elsewhere or behind a proxy.
            for extra in ({"HOST": "0.0.0.0"}, {"TRUST_PROXY": "1"}, {"PUBLIC_BASE_URL": "https://example.com"}):
                proc = self.run_app(data_dir, ADMIN_PASSWORD="123456", ALLOW_WEAK_PASSWORD="1", **extra)
                self.assertNotEqual(proc.returncode, 0, extra)
                self.assertIn("ALLOW_WEAK_PASSWORD", proc.stderr, extra)
        finally:
            shutil.rmtree(data_dir, ignore_errors=True)

    def test_local_env_file_is_read_and_real_environment_wins(self):
        data_dir = tempfile.mkdtemp(prefix="fakhama-test-")
        env_file = Path(data_dir) / "local.env"
        env_file.write_text("# comment\nADMIN_USERNAME=محمد خليل\nADMIN_PASSWORD='123456'\nALLOW_WEAK_PASSWORD=1\n", encoding="utf-8")
        try:
            self.__class__.start_server(data_dir=data_dir, env={"ENV_FILE": str(env_file)})
            self.admin_password = "123456"
            conn = http.client.HTTPConnection("127.0.0.1", self.__class__.port, timeout=10)
            body = json.dumps({"username": "محمد خليل", "password": "123456"}, ensure_ascii=False).encode()
            conn.request("POST", "/api/admin/login", body, {"Content-Type": "application/json; charset=utf-8"})
            res = conn.getresponse()
            payload = res.read()
            self.assertEqual(res.status, 200, payload)
            self.assertEqual(json.loads(payload)["username"], "محمد خليل")
            # A wrong password for the same user is still refused.
            conn.request("POST", "/api/admin/login", json.dumps({"username": "محمد خليل", "password": "654321"}).encode(),
                         {"Content-Type": "application/json"})
            self.assertEqual(conn.getresponse().status, 401)
        finally:
            self.__class__.stop_server()

    def test_no_hardcoded_credentials_in_repo_sources(self):
        for name in ("app.py", "README.md", "index.html", "admin.html"):
            self.assertNotIn("Fakhamah#2026Demo!", (ROOT / name).read_text(encoding="utf-8"), name)


class CatalogueTests(ServerCase):
    def test_owner_photos_are_seeded_as_price_on_request_products(self):
        products = [p for p in self.json("GET", "/api/products")[1]["products"] if p["slug"].startswith("wavy-")]
        self.assertEqual(len(products), 18)
        self.assertEqual(sorted(p["slug"] for p in products), [f"wavy-{n:02d}" for n in range(1, 19)])
        first = self.json("GET", "/api/products")[1]["products"][0]
        self.assertEqual(first["slug"], "wavy-01", "model 1 is listed first")
        for p in products:
            self.assertEqual(p["price"], 0)
            self.assertEqual(p["category"], "curtains")
            self.assertTrue(p["alt"] and p["name"].startswith("ستائر ويفي"))
            self.assertRegex(p["image"], r"^/assets/wavy-\d\d\.jpg$")
            status, resp, body = self.req("GET", p["image"])
            self.assertEqual(status, 200, p["image"])
            self.assertIn("image/jpeg", resp.getheader("Content-Type"))

    def test_photo_files_are_clean_jpegs(self):
        for n in range(1, 19):
            data = (ROOT / "assets" / f"wavy-{n:02d}.jpg").read_bytes()
            self.assertTrue(data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9"), n)
            self.assertNotIn(b"Exif\x00\x00", data, "no camera/screenshot metadata")
            self.assertLess(len(data), 400 * 1024)
        # the hand-off screenshots (which contain a chat window) must never be committed
        self.assertFalse([p for p in ROOT.rglob("*") if p.is_file() and "Screenshot_" in p.name and ".git" not in p.parts])


class CatalogueSeedOnceTests(unittest.TestCase):
    """Existing databases receive the photos once; deleting a product later is not undone by a restart."""

    def run_server(self, data_dir):
        ServerCase.start_server.__func__(ServerCase, data_dir=data_dir, env={"ADMIN_PASSWORD": "Z" + secrets.token_urlsafe(16)})
        ServerCase.stop_server.__func__(ServerCase, remove_data=False)

    def test_seed_runs_once(self):
        import sqlite3
        data_dir = tempfile.mkdtemp(prefix="fakhama-test-")
        try:
            self.run_server(data_dir)
            db = Path(data_dir) / "store.db"
            count = lambda: sqlite3.connect(db).execute("SELECT COUNT(*) FROM products WHERE slug LIKE 'wavy-%'").fetchone()[0]
            self.assertEqual(count(), 18)
            # Simulate a database created before the photos existed (no flag, no wavy rows).
            conn = sqlite3.connect(db)
            conn.execute("DELETE FROM products WHERE slug LIKE 'wavy-%'")
            conn.execute("DELETE FROM settings WHERE key='seed_wavy_v1'")
            conn.commit(); conn.close()
            self.run_server(data_dir)
            self.assertEqual(count(), 18, "photos are added to an existing database")
            # The owner deletes one: it must stay deleted after a restart.
            conn = sqlite3.connect(db); conn.execute("DELETE FROM products WHERE slug='wavy-05'"); conn.commit(); conn.close()
            self.run_server(data_dir)
            self.assertEqual(count(), 17)
        finally:
            shutil.rmtree(data_dir, ignore_errors=True)

    def test_untouched_legacy_samples_are_retired_but_edited_ones_and_orders_are_kept(self):
        import sqlite3
        data_dir = tempfile.mkdtemp(prefix="fakhama-test-")
        try:
            self.run_server(data_dir)
            db = Path(data_dir) / "store.db"
            conn = sqlite3.connect(db)
            conn.execute("DELETE FROM settings WHERE key='sample_cleanup_v1'")
            ts = "2026-01-01T00:00:00+00:00"
            def sample(slug, name, price):
                cur = conn.execute("INSERT INTO products(name,slug,category,description,price,image,alt,badge,sku,stock,featured,active,created_at,updated_at) "
                                   "VALUES(?,?,?,?,?,?,?,?,?,?,1,1,?,?)", (name, slug, "curtains", "x", price, "assets/legacy-sample.jpg", "x", "", "S", 5, ts, ts))
                return cur.lastrowid
            sample("sample-1", "ستائر بلاك أوت تفصيل", 450)                    # untouched, no orders  -> deleted
            sample("sample-2", "ستائر شيفون ناعمة", 999)                        # owner changed the price -> kept
            ordered = sample("sample-3", "ستارة رول بلاك أوت", 280)             # untouched but ordered -> hidden, history kept
            oid = conn.execute("INSERT INTO orders(order_number,customer_name,phone,city,status,subtotal,delivery_fee,total,created_at,updated_at) "
                               "VALUES('MH-X','ع','0551234567','جدة','new',280,0,280,?,?)", (ts, ts)).lastrowid
            conn.execute("INSERT INTO order_items(order_id,product_id,product_name,sku,unit_price,quantity) VALUES(?,?,?,?,?,1)", (oid, ordered, "ستارة رول بلاك أوت", "S", 280))
            conn.commit(); conn.close()
            self.run_server(data_dir)
            conn = sqlite3.connect(db)
            rows = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT slug,active,price FROM products WHERE slug LIKE 'sample-%'")}
            self.assertNotIn("sample-1", rows)
            self.assertEqual(rows["sample-2"], (1, 999))
            self.assertEqual(rows["sample-3"][0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM order_items WHERE product_id=?", (ordered,)).fetchone()[0], 1)
            conn.close()
        finally:
            shutil.rmtree(data_dir, ignore_errors=True)


def totp_now(secret: str, at: float | None = None) -> str:
    """Independent RFC 6238 implementation used to check the server's one."""
    import hashlib, hmac, struct
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", int((time.time() if at is None else at) // 30)), hashlib.sha1).digest()
    off = digest[-1] & 0x0F
    return f"{(struct.unpack('>I', digest[off:off + 4])[0] & 0x7FFFFFFF) % 1000000:06d}"


class GalleryAndCategoryTests(ServerCase):
    def upload(self, kw, raw=PNG, mime="image/png", thumb=True):
        body = {"data": data_uri(mime, raw)}
        if thumb:
            body["thumb"] = data_uri("image/jpeg", JPEG)
        status, data, _ = self.json("POST", "/api/admin/upload", body, **kw)
        self.assertEqual(status, 201, data)
        return data

    def test_upload_endpoint_validates_and_requires_admin(self):
        self.assertEqual(self.json("POST", "/api/admin/upload", {"data": data_uri("image/png", PNG)})[0], 401)
        cookie, csrf = self.login()
        kw = dict(cookie=cookie, csrf=csrf)
        self.assertEqual(self.json("POST", "/api/admin/upload", {"data": "nope"}, **kw)[0], 400)
        self.assertEqual(self.json("POST", "/api/admin/upload", {"data": data_uri("image/png", JPEG)}, **kw)[0], 400)
        up = self.upload(kw)
        self.assertRegex(up["url"], r"^/uploads/[0-9a-f]{32}\.png$")
        self.assertRegex(up["thumb"], r"^/uploads/[0-9a-f]{32}\.jpg$")
        self.assertEqual(self.req("GET", up["url"])[0], 200)
        # bodies above the limit are refused before being read into memory
        huge = b'{"data":"' + b"A" * (10 * 1024 * 1024) + b'"}'
        try:
            self.assertEqual(self.req("POST", "/api/admin/upload", huge, cookie=cookie, csrf=csrf)[0], 413)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the server refused the body and closed the connection before it was fully sent

    def test_product_gallery_order_primary_and_cleanup(self):
        cookie, csrf = self.login()
        kw = dict(cookie=cookie, csrf=csrf)
        a, b, c = self.upload(kw), self.upload(kw), self.upload(kw)
        base = {"name": "منتج معرض صور", "category": "curtains", "price": 100, "stock": 3, "description": "وصف"}
        status, data, _ = self.json("POST", "/api/admin/products", {**base, "images": [a, b, c]}, **kw)
        self.assertEqual(status, 201, data)
        product = data["product"]
        self.assertEqual([i["url"] for i in product["images"]], [a["url"], b["url"], c["url"]])
        self.assertEqual(product["image"], a["url"])
        self.assertEqual(product["thumb"], a["thumb"])
        # Reorder (c first) and drop b: b's files are deleted from disk, a and c are kept.
        status, data, _ = self.json("PUT", f"/api/admin/products/{product['id']}", {**base, "images": [c, a]}, **kw)
        self.assertEqual(status, 200, data)
        self.assertEqual([i["url"] for i in data["product"]["images"]], [c["url"], a["url"]])
        self.assertEqual(data["product"]["image"], c["url"])
        self.assertEqual(self.req("GET", b["url"])[0], 404, "removed upload is deleted")
        self.assertEqual(self.req("GET", b["thumb"])[0], 404)
        self.assertEqual(self.req("GET", a["url"])[0], 200)
        # Public API exposes the gallery in order.
        listed = next(p for p in self.json("GET", "/api/products")[1]["products"] if p["id"] == product["id"])
        self.assertEqual([i["url"] for i in listed["images"]], [c["url"], a["url"]])
        # Forged paths never enter the gallery; an empty gallery is refused; the limit is enforced.
        status, data, _ = self.json("PUT", f"/api/admin/products/{product['id']}", {**base, "images": ["/assets/../store.db", "/uploads/does-not-exist.png", c["url"]]}, **kw)
        self.assertEqual([i["url"] for i in data["product"]["images"]], [c["url"]])
        self.assertEqual(self.json("PUT", f"/api/admin/products/{product['id']}", {**base, "images": []}, **kw)[0], 400)
        many = [self.upload(kw, thumb=False) for _ in range(13)]
        self.assertEqual(self.json("PUT", f"/api/admin/products/{product['id']}", {**base, "images": many}, **kw)[0], 400)
        # Deleting the product deletes its uploads.
        self.assertEqual(self.json("DELETE", f"/api/admin/products/{product['id']}", **kw)[0], 200)
        self.assertEqual(self.req("GET", c["url"])[0], 404)
        for m in many:
            self.req("GET", m["url"])

    def test_category_image_and_reorder(self):
        cookie, csrf = self.login()
        kw = dict(cookie=cookie, csrf=csrf)
        img = self.upload(kw, thumb=False)
        status, data, _ = self.json("POST", "/api/admin/categories", {"name": "قسم بصورة", "image": img["url"]}, **kw)
        self.assertEqual((status, data["image"]), (201, img["url"]), data)
        slug = data["slug"]
        pub = {c["slug"]: c for c in self.json("GET", "/api/categories")[1]["categories"]}
        self.assertEqual(pub[slug]["image"], img["url"])
        # Reorder: reverse the current list.
        current = [c["slug"] for c in self.json("GET", "/api/admin/categories", **kw)[1]["categories"]]
        self.assertEqual(self.json("POST", "/api/admin/categories/reorder", {"order": list(reversed(current))}, **kw)[0], 200)
        self.assertEqual([c["slug"] for c in self.json("GET", "/api/admin/categories", **kw)[1]["categories"]], list(reversed(current)))
        self.assertEqual(self.json("POST", "/api/admin/categories/reorder", {"order": current[:-1]}, **kw)[0], 409, "must list every category")
        self.assertEqual(self.json("POST", "/api/admin/categories/reorder", {"order": "x"}, **kw)[0], 400)
        # Replacing and removing the image.
        from urllib.parse import quote
        url = f"/api/admin/categories/{quote(slug)}"
        img2 = self.upload(kw, thumb=False)
        status, data, _ = self.json("PUT", url, {"name": "قسم بصورة", "image": img2["url"]}, **kw)
        self.assertEqual(data["image"], img2["url"])
        self.assertEqual(self.req("GET", img["url"])[0], 404, "replaced image is deleted")
        status, data, _ = self.json("PUT", url, {"name": "قسم بصورة", "remove_image": True}, **kw)
        self.assertEqual(data["image"], "")
        self.assertEqual(self.req("GET", img2["url"])[0], 404)
        self.json("DELETE", url, **kw)


class StandardsTests(ServerCase):
    def test_csp_uses_nonces_and_no_unsafe_inline_scripts(self):
        for path in ("/", "/admin/", "/privacy", "/offline.html", "/product/wavy-03", "/products", "/categories",
                     "/category/curtains", "/about", "/contact", "/faq", "/no-such-page"):
            status, resp, body = self.req("GET", path)
            self.assertEqual(status, 404 if path == "/no-such-page" else 200, path)
            csp = resp.getheader("Content-Security-Policy")
            nonce = re.search(r"script-src 'self' 'nonce-([^']+)'", csp).group(1)
            self.assertNotIn("'unsafe-inline'", csp.split("style-src-attr")[0], path)
            text = body.decode()
            for tag in re.findall(r"<(?:script|style)\b[^>]*>", text):
                if "ld+json" in tag:
                    continue
                self.assertIn(f'nonce="{nonce}"', tag, (path, tag))
            self.assertIsNone(re.search(r"\son[a-z]+=", text), f"inline event handler in {path}")
        # a new nonce per response
        n1 = re.search(r"nonce-([^']+)", self.req("GET", "/")[1].getheader("Content-Security-Policy")).group(1)
        n2 = re.search(r"nonce-([^']+)", self.req("GET", "/")[1].getheader("Content-Security-Policy")).group(1)
        self.assertNotEqual(n1, n2)

    def test_compression_etag_and_conditional_requests(self):
        status, resp, body = self.req("GET", "/api/products", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(resp.getheader("Content-Encoding"), "gzip")
        import gzip as gz
        self.assertEqual(len(json.loads(gz.decompress(body))["products"]), 18)
        self.assertLess(len(body), 8000, "catalogue JSON is compressed")
        etag = resp.getheader("ETag")
        self.assertTrue(etag)
        self.assertEqual(resp.getheader("Vary"), "Accept-Encoding")
        status, resp2, body2 = self.req("GET", "/api/products", headers={"If-None-Match": etag, "Accept-Encoding": "gzip"})
        self.assertEqual((status, body2), (304, b""))
        status, resp, body = self.req("GET", "/assets/wavy-01.jpg")
        self.assertIn("max-age", resp.getheader("Cache-Control"))
        asset_etag = resp.getheader("ETag")
        self.assertEqual(self.req("GET", "/assets/wavy-01.jpg", headers={"If-None-Match": asset_etag})[0], 304)
        _, resp, _ = self.req("GET", "/api/admin/me")
        self.assertEqual(resp.getheader("Cache-Control"), "no-store")
        self.assertEqual(self.req("GET", "/")[1].getheader("Cache-Control"), "no-cache")
        self.assertEqual(resp.getheader("Cross-Origin-Opener-Policy"), "same-origin")
        self.assertTrue(resp.getheader("X-Request-ID"))

    def test_health_reports_database_and_version(self):
        status, data, _ = self.json("GET", "/api/health")
        self.assertEqual((status, data["ok"], data["db"]), (200, True, True))
        self.assertRegex(data["version"], r"^\d+\.\d+\.\d+$")

    def test_product_page_has_server_rendered_seo_and_structured_data(self):
        status, resp, body = self.req("GET", "/product/wavy-03", headers={"Host": "shop.example.org"})
        self.assertEqual(status, 200)
        page = body.decode()
        self.assertIn("<title>ستائر ويفي تفصيل حسب الطلب — موديل 3 |", page)
        self.assertIn('property="og:type" content="product"', page)
        self.assertIn('property="og:image" content="http://shop.example.org/assets/wavy-03.jpg"', page)
        self.assertIn('name="twitter:card"', page)
        self.assertIn('content="noindex,nofollow"', page, "stays noindex until the owner marks the store ready")
        blocks = [json.loads(b) for b in re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', page, re.S)]
        types = [b["@type"] for b in blocks]
        self.assertIn("Product", types)
        self.assertIn("BreadcrumbList", types)
        product = next(b for b in blocks if b["@type"] == "Product")
        self.assertNotIn("offers", product, "price on request: no invented price")
        self.assertEqual(product["sku"], "FAL-WAV-003")
        # unknown product -> 404 + noindex, still a usable storefront
        status, resp, body = self.req("GET", "/product/does-not-exist")
        self.assertEqual(status, 404)
        self.assertIn(b'content="noindex,nofollow"', body)
        # a priced product publishes an Offer
        cookie, csrf = self.login()
        kw = dict(cookie=cookie, csrf=csrf)
        status, made, _ = self.json("POST", "/api/admin/products", {"name": "ستارة بسعر", "category": "curtains", "price": 350, "stock": 4, "image": "/assets/wavy-04.jpg", "description": "<b>x</b> & y"}, **kw)
        slug = made["product"]["slug"]
        from urllib.parse import quote
        _, _, body = self.req("GET", "/product/" + quote(slug))
        offer = next(json.loads(b) for b in re.findall(r'<script[^>]*ld\+json[^>]*>(.*?)</script>', body.decode(), re.S) if '"Product"' in b)["offers"]
        self.assertEqual((offer["price"], offer["priceCurrency"], offer["availability"]), ("350", "SAR", "https://schema.org/InStock"))
        self.assertNotIn("<b>x</b>", body.decode(), "descriptions are escaped in meta tags")
        self.json("DELETE", f"/api/admin/products/{made['product']['id']}", **kw)

    def test_sitemap_lists_products_only_when_ready(self):
        cookie, csrf = self.login()
        kw = dict(cookie=cookie, csrf=csrf)
        self.json("PUT", "/api/admin/settings", {"site_ready": True}, **kw)
        try:
            status, resp, body = self.req("GET", "/sitemap.xml", headers={"Host": "shop.example.org"})
            self.assertEqual(status, 200)
            text = body.decode()
            # home + 5 store pages + 2 legal pages + 1 category with products + 18 products
            self.assertEqual(text.count("<url>"), 1 + 5 + 2 + 1 + 18)
            self.assertIn("<loc>http://shop.example.org/product/wavy-01</loc>", text)
            for page in ("/products", "/categories", "/about", "/contact", "/faq", "/category/curtains"):
                self.assertIn(f"<loc>http://shop.example.org{page}</loc>", text)
            self.assertNotIn("/category/fabrics", text, "a category without products is not listed")
            self.assertIn("<image:loc>http://shop.example.org/assets/wavy-01.jpg</image:loc>", text)
            self.assertIn("/privacy", text)
            _, _, page = self.req("GET", "/", headers={"Host": "shop.example.org"})
            self.assertIn(b'rel="canonical" href="http://shop.example.org/"', page)
            self.assertIn(b'content="index,follow,max-image-preview:large"', page)
            self.assertIn(b'hreflang="ar-SA"', page)
            self.assertIn(b'rel="preload" as="image"', page)
        finally:
            self.json("PUT", "/api/admin/settings", {"site_ready": False}, **kw)

    def test_legal_pages(self):
        for path, title in (("/privacy", "سياسة الخصوصية"), ("/terms", "الشروط والأحكام")):
            status, resp, body = self.req("GET", path)
            self.assertEqual(status, 200)
            self.assertIn(title, body.decode())
            self.assertIn("+966 57 648 6491", body.decode(), "contact details come from the store settings")
            self.assertIn('lang="ar"', body.decode())

    def test_order_requires_consent_and_is_idempotent(self):
        cookie, csrf = self.login()
        kw = dict(cookie=cookie, csrf=csrf)
        status, made, _ = self.json("POST", "/api/admin/products", {"name": "منتج للطلب", "category": "curtains", "price": 100, "stock": 10, "image": "/assets/wavy-05.jpg"}, **kw)
        pid = made["product"]["id"]
        order = {"name": "عميل", "phone": "0551234567", "city": "جدة", "items": [{"product_id": pid, "quantity": 1}]}
        status, data, _ = self.json("POST", "/api/orders", order)
        self.assertEqual(status, 400)
        self.assertIn("الخصوصية", data["error"])
        status, data, _ = self.json("POST", "/api/orders", {**order, "consent": "yes"})
        self.assertEqual(status, 400, "consent must be the boolean true")
        key = "retry-key-" + secrets.token_hex(6)
        status, first, _ = self.json("POST", "/api/orders", {**order, "consent": True}, headers={"Idempotency-Key": key})
        self.assertEqual(status, 201)
        status, again, resp = self.json("POST", "/api/orders", {**order, "consent": True}, headers={"Idempotency-Key": key})
        self.assertEqual(status, 200)
        self.assertEqual(again["order_number"], first["order_number"])
        self.assertEqual(resp.getheader("Idempotent-Replayed"), "true")
        stock = next(p["stock"] for p in self.json("GET", "/api/products")[1]["products"] if p["id"] == pid)
        self.assertEqual(stock, 9, "the retry did not take a second unit")
        self.assertEqual(self.json("POST", "/api/orders", {**order, "consent": True}, headers={"Idempotency-Key": "short"})[0], 400)
        self.json("DELETE", f"/api/admin/products/{pid}", **kw)


class AuditAndDashboardTests(ServerCase):
    def test_admin_actions_are_audited_without_secrets(self):
        cookie, csrf = self.login()
        kw = dict(cookie=cookie, csrf=csrf)
        self.assertEqual(self.json("GET", "/api/admin/audit")[0], 401)
        self.json("POST", "/api/admin/login", {"username": "admin", "password": "wrong-password-xyz"})
        status, made, _ = self.json("POST", "/api/admin/products", {"name": "منتج للتدقيق", "category": "curtains", "price": 5, "stock": 1, "image": "/assets/wavy-06.jpg"}, **kw)
        self.json("DELETE", f"/api/admin/products/{made['product']['id']}", **kw)
        self.json("PUT", "/api/admin/settings", {"tagline": "عبارة"}, **kw)
        self.json("POST", "/api/admin/categories", {"name": "قسم تدقيق"}, **kw)
        status, log, _ = self.json("GET", "/api/admin/audit", **kw)
        self.assertEqual(status, 200)
        actions = [e["action"] for e in log["entries"]]
        for expected in ("login", "login_failed", "product_create", "product_delete", "settings_update", "category_change"):
            self.assertIn(expected, actions)
        text = json.dumps(log, ensure_ascii=False)
        self.assertNotIn(self.admin_password, text)
        self.assertNotIn("wrong-password-xyz", text)
        self.assertNotIn(csrf, text)
        self.assertTrue(all(e["ip"] for e in log["entries"]))

    def test_dashboard_has_daily_series_and_low_stock_items(self):
        cookie, csrf = self.login()
        kw = dict(cookie=cookie, csrf=csrf)
        status, made, _ = self.json("POST", "/api/admin/products", {"name": "منتج مخزون منخفض", "category": "curtains", "price": 10, "stock": 2, "image": "/assets/wavy-07.jpg"}, **kw)
        status, order, _ = self.json("POST", "/api/orders", {"name": "عميل", "phone": "0551234567", "city": "جدة", "consent": True, "items": [{"product_id": made["product"]["id"], "quantity": 1}]})
        self.assertEqual(status, 201)
        _, dash, _ = self.json("GET", "/api/admin/dashboard", **kw)
        self.assertEqual(len(dash["daily"]), 14)
        self.assertEqual(dash["daily"][-1]["orders"], 1)
        self.assertEqual(dash["daily"][-1]["total"], 10)
        self.assertIn(made["product"]["id"], [i["id"] for i in dash["low_stock_items"]])
        self.json("DELETE", f"/api/admin/products/{made['product']['id']}", **kw)


class TwoFactorTests(ServerCase):
    def test_totp_matches_rfc_6238_vectors(self):
        sys.path.insert(0, str(ROOT))
        import totp
        secret = base64.b32encode(b"12345678901234567890").decode().rstrip("=")  # RFC 6238 SHA-1 test key
        for at, expected in ((59, "287082"), (1111111109, "081804"), (1111111111, "050471"), (1234567890, "005924"), (2000000000, "279037")):
            self.assertEqual(totp.code_at(secret, at), expected)
            self.assertTrue(totp.verify(secret, expected, at))
        self.assertFalse(totp.verify(secret, "000000", 59))
        self.assertFalse(totp.verify(secret, "", 59))

    def test_two_factor_login_flow(self):
        cookie, csrf = self.login()
        kw = dict(cookie=cookie, csrf=csrf)
        self.assertFalse(self.json("GET", "/api/admin/me", cookie=cookie)[1]["totp_enabled"])
        self.assertEqual(self.json("POST", "/api/admin/2fa/enable", {"code": "123456"}, **kw)[0], 409, "setup first")
        status, setup, _ = self.json("POST", "/api/admin/2fa/setup", {}, **kw)
        self.assertEqual(status, 200)
        self.assertTrue(setup["otpauth"].startswith("otpauth://totp/"))
        self.assertEqual(self.json("POST", "/api/admin/2fa/enable", {"code": "000000"}, **kw)[0], 400)
        self.assertEqual(self.json("POST", "/api/admin/2fa/enable", {"code": totp_now(setup["secret"])}, **kw)[0], 200)
        self.assertTrue(self.json("GET", "/api/admin/me", cookie=cookie)[1]["totp_enabled"])
        # Password alone no longer logs in.
        status, data, _ = self.json("POST", "/api/admin/login", {"username": "admin", "password": self.admin_password})
        self.assertEqual((status, data.get("need_code")), (401, True))
        status, data, _ = self.json("POST", "/api/admin/login", {"username": "admin", "password": self.admin_password, "code": "000000"})
        self.assertEqual((status, data.get("need_code")), (401, True))
        status, data, resp = self.json("POST", "/api/admin/login", {"username": "admin", "password": self.admin_password, "code": totp_now(setup["secret"])})
        self.assertEqual(status, 200, data)
        cookies = [v for k, v in resp.getheaders() if k.lower() == "set-cookie"]
        self.assertTrue(any(c.startswith("fakhama_admin=") and "HttpOnly" in c for c in cookies))
        self.assertTrue(any(c.startswith("fakhama_admin_flag=1") and "HttpOnly" not in c for c in cookies), "readable marker cookie for the storefront admin bar")
        new_cookie = cookies[0].split(";")[0]
        # Disable needs the password and a valid code.
        kw2 = dict(cookie=new_cookie, csrf=data["csrf"])
        self.assertEqual(self.json("POST", "/api/admin/2fa/disable", {"password": "wrong", "code": totp_now(setup["secret"])}, **kw2)[0], 403)
        self.assertEqual(self.json("POST", "/api/admin/2fa/disable", {"password": self.admin_password, "code": totp_now(setup["secret"])}, **kw2)[0], 200)
        self.assertEqual(self.json("POST", "/api/admin/login", {"username": "admin", "password": self.admin_password})[0], 200)


class BackupTests(unittest.TestCase):
    def test_backup_command_creates_consistent_copies_and_prunes(self):
        import sqlite3
        data_dir = tempfile.mkdtemp(prefix="fakhama-test-")
        try:
            env = {**os.environ, "DATA_DIR": data_dir, "ADMIN_PASSWORD": "Z" + secrets.token_urlsafe(16), "BACKUP_KEEP": "2"}
            (Path(data_dir) / "uploads").mkdir(exist_ok=True)
            (Path(data_dir) / "uploads" / "x.png").write_bytes(PNG)
            for _ in range(3):
                out = subprocess.run([sys.executable, "-I", str(ROOT / "app.py"), "backup"], capture_output=True, text=True, env=env, timeout=60)
                self.assertEqual(out.returncode, 0, out.stderr)
                time.sleep(1.1)
            backups = sorted((Path(data_dir) / "backups").glob("store-*.db"))
            self.assertEqual(len(backups), 2, "only the newest BACKUP_KEEP are kept")
            self.assertEqual(sqlite3.connect(backups[-1]).execute("SELECT COUNT(*) FROM products").fetchone()[0], 18)
            self.assertEqual(sqlite3.connect(backups[-1]).execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(len(list((Path(data_dir) / "backups").glob("uploads-*.tar.gz"))), 2)
        finally:
            shutil.rmtree(data_dir, ignore_errors=True)


def main_of(html: str) -> str:
    """The page's own content (between the shell's main markers)."""
    return html.split("<!--main:start-->", 1)[1].split("<!--main:end-->", 1)[0]


def ld_blocks(html: str) -> list[dict]:
    return [json.loads(b) for b in re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S)]


def handler_attributes(html: str) -> list[str]:
    """Real on*= attributes in the markup (escaped text that merely contains 'onerror=' does not count)."""
    from html.parser import HTMLParser
    found: list[str] = []

    class Collector(HTMLParser):
        def handle_starttag(self, tag, attrs):
            found.extend(f"{tag}[{name}]" for name, _ in attrs if name.lower().startswith("on"))
    Collector().feed(html)
    return found


def ld_type(block: dict) -> str:
    kind = block.get("@type")
    return kind if isinstance(kind, str) else "+".join(kind)


class StorePagesTests(ServerCase):
    """About, products, categories, one category, one product, contact, FAQ, branded 404 — all inside the shared shell."""
    PAGES = {"/products": "products", "/categories": "categories", "/category/curtains": "category", "/product/wavy-03": "product",
             "/about": "about", "/contact": "contact", "/faq": "faq"}

    def page(self, path: str, **kw) -> tuple[int, http.client.HTTPResponse, str]:
        status, resp, body = self.req("GET", path, **kw)
        return status, resp, body.decode()

    def test_every_page_renders_inside_the_shared_shell(self):
        for path, name in self.PAGES.items():
            status, resp, html = self.page(path)
            self.assertEqual(status, 200, path)
            self.assertIn("text/html", resp.getheader("Content-Type"))
            self.assertEqual(len(re.findall(r"<h1\b", html)), 1, f"one <h1> on {path}")
            self.assertIn(f'<body data-page="{name}"', html)
            main = main_of(html)
            crumbs = re.search(r'<nav class="breadcrumbs" aria-label="مسار التنقل"><ol>(.*?)</ol></nav>', main, re.S)
            self.assertTrue(crumbs, path)
            items = re.findall(r"<li>(.*?)</li>", crumbs.group(1))
            self.assertGreaterEqual(len(items), 2, path)
            self.assertIn('href="/"', items[0])
            self.assertIn('aria-current="page"', items[-1], "the current page is the last crumb")
            self.assertNotIn("<a ", items[-1], "the current crumb is not a link")
            self.assertEqual(crumbs.group(1).count("aria-current"), 1)
            self.assertTrue(main.lstrip().startswith('<section class="page-hero'), path)
            # the shell is intact: header, search, cart, favourites, dialogs and scripts work on every page
            for hook in ('id="searchInput"', 'id="cartOpen"', 'id="cartCount"', 'id="favoritesToggle"', 'id="productModal"',
                         'id="orderModal"', 'id="orderForm"', 'id="adminBar"', 'id="cartDrawer"', '<style nonce=', 'id="pageStyles"'):
                self.assertIn(hook, html, (path, hook))
            ids = re.findall(r'\sid="([^"]+)"', html)
            self.assertEqual(len(ids), len(set(ids)), f"duplicate ids on {path}: {[i for i in ids if ids.count(i) > 1]}")
            self.assertNotIn("javascript:", html.lower())
            self.assertIn("<!--main:start-->", html)
            self.assertEqual(self.req("HEAD", path)[0], 200, path)

    def test_structured_data_and_meta_per_page(self):
        expected = {"/products": {"BreadcrumbList", "CollectionPage"}, "/categories": {"BreadcrumbList", "CollectionPage"},
                    "/category/curtains": {"BreadcrumbList", "CollectionPage"}, "/product/wavy-03": {"Product", "BreadcrumbList"},
                    "/about": {"BreadcrumbList", "AboutPage"}, "/contact": {"BreadcrumbList", "ContactPage"}, "/faq": {"BreadcrumbList", "FAQPage"}}
        for path, types in expected.items():
            _, _, html = self.page(path, headers={"Host": "shop.example.org"})
            blocks = ld_blocks(html)
            self.assertEqual({ld_type(b) for b in blocks}, types, path)
            self.assertIn('content="noindex,nofollow"', html, "noindex until the owner marks the store ready")
            self.assertIn(f'property="og:url" content="http://shop.example.org{path}"', html)
            self.assertRegex(html, r"<title>[^<]+ \| الفخامة للأقمشة والستائر</title>")
        _, _, html = self.page("/product/wavy-03", headers={"Host": "shop.example.org"})
        crumbs = next(b for b in ld_blocks(html) if b["@type"] == "BreadcrumbList")["itemListElement"]
        self.assertEqual([c["name"] for c in crumbs], ["الرئيسية", "ستائر تفصيل", "ستائر ويفي تفصيل حسب الطلب — موديل 3"])
        self.assertEqual([c["item"] for c in crumbs], ["http://shop.example.org/", "http://shop.example.org/category/curtains",
                                                       "http://shop.example.org/product/wavy-03"])
        _, _, html = self.page("/products")
        listing = next(b for b in ld_blocks(html) if b["@type"] == "CollectionPage")["mainEntity"]
        self.assertEqual((listing["@type"], listing["numberOfItems"]), ("ItemList", 18))
        _, _, html = self.page("/faq")
        faq = next(b for b in ld_blocks(html) if b["@type"] == "FAQPage")
        sys.path.insert(0, str(ROOT))
        import pages
        self.assertEqual([(q["name"], q["acceptedAnswer"]["text"]) for q in faq["mainEntity"]], pages.FAQ)
        # once ready: indexable, canonical, and a search result page stays out of the index
        cookie, csrf = self.login()
        self.json("PUT", "/api/admin/settings", {"site_ready": True}, cookie=cookie, csrf=csrf)
        try:
            _, _, html = self.page("/about", headers={"Host": "shop.example.org"})
            self.assertIn('rel="canonical" href="http://shop.example.org/about"', html)
            self.assertIn('content="index,follow,max-image-preview:large"', html)
            _, _, html = self.page("/products?q=" + quote_url("موديل"))
            self.assertIn('content="noindex,nofollow"', html)
            status, _, html = self.page("/no-such-page")
            self.assertEqual(status, 404)
            self.assertIn('content="noindex,nofollow"', html)
            self.assertIn('<link id="canonicalLink" rel="canonical">', html)
        finally:
            self.json("PUT", "/api/admin/settings", {"site_ready": False}, cookie=cookie, csrf=csrf)

    def test_products_page_server_renders_the_catalogue(self):
        _, _, html = self.page("/products")
        main = main_of(html)
        self.assertIn('id="shop"', main)
        self.assertIn('<div class="filter-list" id="filterList" role="group" aria-label="تصفية حسب القسم">', main)
        self.assertIn('data-filter="all"', main)
        self.assertIn('data-filter="curtains"', main)
        self.assertIn('<span class="result-count" id="resultCount" aria-live="polite">18 منتجات</span>', main)
        self.assertIn('<select class="sort-select" id="sortSelect">', main)
        for value in ("featured", "low", "high", "name"):
            self.assertIn(f'<option value="{value}">', main)
        grid = re.search(r'<div class="product-grid" id="productGrid"[^>]*>(.*)</div>\s*<p class="product-footnote"', main, re.S).group(1)
        self.assertEqual(grid.count('<article class="product-card">'), 18)
        self.assertIn('<a class="product-link" href="/product/wavy-01">ستائر ويفي تفصيل حسب الطلب — موديل 1</a>', grid)
        self.assertEqual(len(re.findall(r'data-quick="\d+"', grid)), 18)
        self.assertEqual(len(re.findall(r'data-favorite="\d+"', grid)), 18)
        self.assertIn('<span class="on-request">السعر حسب الطلب</span>', grid)
        # ?q= filters the server-rendered grid (same haystack as the script) and fills the header search box
        _, _, html = self.page("/products?q=" + quote_url("موديل 1"))
        main = main_of(html)
        self.assertEqual(main.count('<article class="product-card">'), 10, "موديل 1 and 10..18")
        self.assertIn('id="searchInput"', html)
        self.assertRegex(html, r'<input id="searchInput"[^>]*value="موديل 1"')
        _, _, html = self.page("/products?q=" + quote_url('"><script>alert(1)</script>'))
        self.assertNotIn("<script>alert(1)", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", main_of(html))
        self.assertIn('class="no-results"', main_of(html))

    def test_categories_and_category_pages_follow_the_catalogue(self):
        _, _, html = self.page("/categories")
        index = re.search(r'<div class="category-grid" id="categoryIndex">(.*?)</div></section>', main_of(html), re.S).group(1)
        self.assertEqual(index.count('class="category-card"'), 1, "only categories that hold products")
        self.assertIn('href="/category/curtains"', index)
        self.assertIn("<h3>ستائر تفصيل</h3><p>18 منتج</p>", index)
        self.assertEqual(self.page("/category/fabrics")[0], 200, "an active category without products still answers")
        self.assertIn('content="noindex,nofollow"', self.page("/category/fabrics")[2])
        self.assertIn("لا توجد منتجات في هذا القسم حاليًا", main_of(self.page("/category/fabrics")[2]))
        cookie, csrf = self.login()
        kw = dict(cookie=cookie, csrf=csrf)
        status, made, _ = self.json("POST", "/api/admin/products", {"name": "قماش كتان", "category": "fabrics", "price": 95, "stock": 3,
                                                                    "image": "/assets/wavy-06.jpg"}, **kw)
        self.assertEqual(status, 201, made)
        try:
            index = main_of(self.page("/categories")[2])
            self.assertEqual(index.count('class="category-card"'), 2)
            status, _, html = self.page("/category/fabrics")
            self.assertEqual(status, 200)
            self.assertIn('<body data-page="category" data-category="fabrics">', html)
            main = main_of(html)
            self.assertIn('<h1 id="pageTitle">أقمشة الستائر</h1>', main)
            self.assertNotIn('id="filterList"', main, "a category page filters to body[data-category]")
            self.assertIn('id="productGrid"', main)
            self.assertIn('id="sortSelect"', main)
            self.assertEqual(main.count('<article class="product-card">'), 1)
            self.assertIn("٩٥ <small>ر.س</small>", main)
            switch = re.search(r'<nav class="category-switch" aria-label="الأقسام">(.*?)</nav>', main, re.S).group(1)
            self.assertIn('href="/category/curtains"', switch)
            self.assertRegex(switch, r'href="/category/fabrics" aria-current="page"')
            self.assertEqual(switch.count("aria-current"), 1)
            self.assertIn('class="page-hero-media" src="/assets/wavy-06.jpg"', main, "backdrop falls back to the first product photo")
            # a hidden category is gone: branded 404
            self.assertEqual(self.json("PUT", "/api/admin/categories/fabrics", {"name": "أقمشة الستائر", "active": False}, **kw)[0], 200)
            status, _, html = self.page("/category/fabrics")
            self.assertEqual(status, 404)
            self.assertIn('data-page="notfound"', html)
            self.assertEqual(main_of(self.page("/categories")[2]).count('class="category-card"'), 1)
        finally:
            self.json("PUT", "/api/admin/categories/fabrics", {"name": "أقمشة الستائر", "active": True}, **kw)
            self.json("DELETE", f"/api/admin/products/{made['product']['id']}", **kw)
        self.assertEqual(self.page("/category/does-not-exist")[0], 404)

    def test_product_page_markup_and_states(self):
        status, _, html = self.page("/product/wavy-03", headers={"Host": "shop.example.org"})
        self.assertEqual(status, 200)
        pid = re.search(r'data-product-id="(\d+)" data-product-slug="wavy-03"', html).group(1)
        main = main_of(html)
        self.assertIn(f'<article class="product-page" aria-labelledby="productTitle" data-product-id="{pid}">', main)
        self.assertIn('<img id="pageGalleryMain" src="/assets/wavy-03.jpg"', main)
        self.assertNotIn('class="gthumb', main.split('id="relatedGrid"')[0], "one photo: no thumbnails")
        self.assertIn('<h1 id="productTitle">ستائر ويفي تفصيل حسب الطلب — موديل 3</h1>', main)
        self.assertIn('<a class="eyebrow product-page-category" href="/category/curtains">ستائر تفصيل</a>', main)
        self.assertIn('<span class="on-request">السعر حسب الطلب</span>', main)
        self.assertRegex(main, rf'<button class="btn btn-primary" type="button" data-add-product="{pid}">أضف إلى السلة')
        self.assertIn('data-copy-link="http://shop.example.org/product/wavy-03"', main)
        self.assertIn('href="https://wa.me/?text=', main, "share link (not the store's number)")
        self.assertIn("تفصيل الستائر حسب المقاس؛ تواصل مع المتجر لتأكيد التوصيل والتركيب بحسب موقعك.", main, "delivery note from the settings")
        related = re.search(r'<div class="product-grid" id="relatedGrid">(.*?)</div></div></section>', main, re.S).group(1)
        self.assertEqual(related.count('<article class="product-card">'), 4)
        self.assertNotIn(f'data-quick="{pid}"', related, "the product itself is not related to itself")
        # a priced, out-of-stock product with a gallery
        cookie, csrf = self.login()
        kw = dict(cookie=cookie, csrf=csrf)
        status, made, _ = self.json("POST", "/api/admin/products", {"name": "ستارة معرض", "category": "curtains", "price": 1450, "stock": 0,
                                                                    "images": ["/assets/wavy-04.jpg", "/assets/wavy-05.jpg"], "sku": "G-1"}, **kw)
        self.assertEqual(status, 201, made)
        product = made["product"]
        try:
            main = main_of(self.page("/product/" + quote_url(product["slug"]))[2])
            thumbs = re.findall(r'<button type="button" class="gthumb( active)?" data-page-gindex="(\d)" data-full="([^"]+)" aria-label="[^"]+" aria-pressed="(true|false)">', main)
            self.assertEqual([(t[1], t[2], t[3]) for t in thumbs], [("0", "/assets/wavy-04.jpg", "true"), ("1", "/assets/wavy-05.jpg", "false")])
            self.assertIn('src="/assets/wavy-04-t.jpg"', main, "thumbnails use the small photo")
            self.assertIn("١٬٤٥٠ <small>ريال سعودي</small>", main)
            self.assertRegex(main, rf'data-add-product="{product["id"]}" disabled>غير متوفر حاليًا</button>')
            self.assertIn('class="product-page-stock is-out"', main)
            self.assertIn('<span dir="ltr">G-1</span>', main)
            # hidden -> branded 404
            self.json("PUT", f"/api/admin/products/{product['id']}", {"name": "ستارة معرض", "category": "curtains", "price": 1450, "stock": 0,
                                                                     "images": ["/assets/wavy-04.jpg"], "active": False}, **kw)
            status, _, html = self.page("/product/" + quote_url(product["slug"]))
            self.assertEqual(status, 404)
            self.assertIn("هذا المنتج غير متاح حاليًا", html)
        finally:
            self.json("DELETE", f"/api/admin/products/{product['id']}", **kw)

    def test_about_contact_and_faq_content(self):
        sys.path.insert(0, str(ROOT))
        import pages
        main = main_of(self.page("/about")[2])
        self.assertIn('<h1 id="pageTitle">من نحن</h1>', main)
        prose = re.search(r'<div class="prose">(.*?)</div>', main, re.S).group(1)
        self.assertEqual(prose.count("<p>"), 3, "the default text has three paragraphs")
        self.assertEqual(main.count('<article class="service-card">'), 5)
        for title in ("تفصيل ستائر حسب المقاس", "ستائر رول", "ستائر شرائح معدنية", "ستائر كهربائية", "أقمشة الستائر"):
            self.assertIn(f"<h3>{title}</h3>", main)
        self.assertIn('href="/category/curtains">تصفّح ستائر تفصيل', main, "a service links to its category only when it has products")
        self.assertNotIn('href="/category/roller"', main)
        self.assertIn('href="tel:+966576486491"', main)
        self.assertIn('href="https://maps.app.goo.gl/kn2kxuPujQT9raLG7?g_st=awb"', main)
        self.assertIn('href="/products"', main)
        self.assertIn('href="/contact"', main)
        for path in ("/about", "/contact"):
            text = main_of(self.page(path)[2])
            self.assertNotIn("wa.me/", text, "no WhatsApp action while the owner has not set a number")
            self.assertNotIn("أوقات العمل", text, "no opening hours until the owner sets them")
        # contact form: labelled fields, autocomplete, honeypot, live status
        main = main_of(self.page("/contact")[2])
        form = re.search(r'<form id="contactForm"[^>]*>(.*?)</form>', main, re.S).group(1)
        for field_id in re.findall(r'<(?:input|textarea)\b[^>]*\bid="([^"]+)"', form):
            self.assertIn(f'<label for="{field_id}">', form, field_id)
        self.assertRegex(form, r'<input id="contactName" name="name" required[^>]*autocomplete="name"')
        self.assertRegex(form, r'<input id="contactPhone" name="phone" type="tel" inputmode="tel" required[^>]*autocomplete="tel"')
        self.assertRegex(form, r'<input id="contactEmail" name="email" type="email"')
        self.assertNotRegex(form, r'id="contactEmail"[^>]*required')
        self.assertRegex(form, r'<textarea id="contactMessage" name="message" required')
        self.assertRegex(form, r'<input type="checkbox" name="consent" value="1" required>')
        self.assertIn('href="/privacy"', form)
        self.assertRegex(form, r'<div class="hp" aria-hidden="true">.*<input id="contactWebsite" name="website" type="text" tabindex="-1" autocomplete="off">')
        self.assertIn('<p id="contactStatus" class="form-status" role="status" aria-live="polite">', form)
        self.assertRegex(main, r'<noscript>.*tel:\+966576486491.*</noscript>')
        # opening hours and WhatsApp appear once the owner sets them
        cookie, csrf = self.login()
        kw = dict(cookie=cookie, csrf=csrf)
        self.json("PUT", "/api/admin/settings", {"opening_hours": "السبت–الخميس\n<b>9ص</b>", "whatsapp": "+966 50 000 0000"}, **kw)
        try:
            main = main_of(self.page("/contact")[2])
            self.assertIn("أوقات العمل", main)
            self.assertIn("السبت–الخميس<br>&lt;b&gt;9ص&lt;/b&gt;", main)
            self.assertIn('href="https://wa.me/966500000000"', main)
        finally:
            self.json("PUT", "/api/admin/settings", {"opening_hours": "", "whatsapp": ""}, **kw)
        # FAQ: one source for /faq and the home page
        main = main_of(self.page("/faq")[2])
        self.assertEqual(re.findall(r"<summary>(.*?)</summary>", main), [q for q, _ in pages.FAQ])
        self.assertIn('href="/contact"', main)
        home = self.page("/")[2]
        block = home.split("<!--faq:start-->")[1].split("<!--faq:end-->")[0]
        self.assertEqual(re.findall(r"<summary>(.*?)</summary>", block), [q for q, _ in pages.FAQ])
        self.assertIn('<body data-page="home">', home, "the home page keeps its own shell")

    def test_hostile_text_is_escaped_on_every_page(self):
        cookie, csrf = self.login()
        kw = dict(cookie=cookie, csrf=csrf)
        evil = '"><script>alert(1)</script><img src=x onerror=alert(2)>'
        status, data, _ = self.json("PUT", "/api/admin/settings", {"about_title": evil, "about_body": evil + "\n\n" + evil, "opening_hours": evil,
                                                                   "tagline": evil, "delivery_note": evil}, **kw)
        self.assertEqual(status, 200, data)
        status, cat, _ = self.json("POST", "/api/admin/categories", {"name": "قسم " + evil[:40], "description": evil}, **kw)
        self.assertEqual(status, 201, cat)
        status, made, _ = self.json("POST", "/api/admin/products", {"name": "ستارة " + evil, "category": cat["slug"], "price": 10, "stock": 2,
                                                                    "description": evil, "badge": evil[:50], "alt": evil, "sku": evil[:60],
                                                                    "image": "/assets/wavy-07.jpg"}, **kw)
        self.assertEqual(status, 201, made)
        try:
            from urllib.parse import quote
            paths = ["/about", "/contact", "/faq", "/products", "/categories", "/category/" + quote(cat["slug"]),
                     "/product/" + quote(made["product"]["slug"]), "/product/wavy-02"]
            for path in paths:
                status, _, html = self.page(path)
                self.assertEqual(status, 200, path)
                self.assertNotIn("<script>alert", html, path)
                self.assertNotIn("<img src=x", html, path)
                self.assertEqual(handler_attributes(html), [], f"live event handler attribute on {path}")
            self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", main_of(self.page("/about")[2]))
        finally:
            self.json("DELETE", f"/api/admin/products/{made['product']['id']}", **kw)
            from urllib.parse import quote
            self.json("DELETE", "/api/admin/categories/" + quote(cat["slug"]), **kw)
            self.json("PUT", "/api/admin/settings", {"about_title": "من نحن", "about_body": DEFAULT_ABOUT, "opening_hours": "",
                                                     "tagline": "تفصيل ستائر وأقمشة في جدة",
                                                     "delivery_note": "تفصيل الستائر حسب المقاس؛ تواصل مع المتجر لتأكيد التوصيل والتركيب بحسب موقعك."}, **kw)

    def test_not_found_and_redirects(self):
        for path in ("/no-such-page", "/products/extra", "/category/", "/product/", "/.git/config", "/about/team"):
            status, resp, html = self.page(path)
            self.assertEqual(status, 404, path)
            self.assertIn("text/html", resp.getheader("Content-Type"))
            self.assertIn('<body data-page="notfound">', html)
            self.assertIn('content="noindex,nofollow"', html)
            self.assertEqual(len(re.findall(r"<h1\b", html)), 1)
            main = main_of(html)
            self.assertIn('<h1 id="pageTitle">الصفحة غير موجودة</h1>', main)
            self.assertRegex(main, r'<form class="notfound-search" role="search" action="/products" method="get">')
            self.assertIn('<label for="notFoundSearch">', main)
            self.assertIn('<input id="notFoundSearch" name="q" type="search"', main)
            for href in ("/", "/products", "/categories", "/contact"):
                self.assertIn(f'<a href="{href}">', main)
        self.assertEqual(self.req("HEAD", "/no-such-page")[0], 404)
        status, resp, body = self.req("GET", "/missing.png")
        self.assertEqual((status, resp.getheader("Content-Type")), (404, "text/plain; charset=utf-8"), "files keep a plain 404")
        self.assertEqual(self.req("GET", "/assets/missing")[0], 404)
        status, data, _ = self.json("GET", "/api/no-such-endpoint")
        self.assertEqual(status, 404)
        self.assertIn("error", data)
        from urllib.parse import quote
        arabic = quote("ستائر-رول")
        for path, location in (("/about/", "/about"), ("/products/", "/products"), ("/products/?q=abc", "/products?q=abc"),
                               ("/categories//", "/categories"), ("/contact/", "/contact"), ("/faq/", "/faq"), ("/privacy/", "/privacy"),
                               ("/category/curtains/", "/category/curtains"), ("/product/wavy-03/", "/product/wavy-03"),
                               (f"/category/{arabic}/", f"/category/{arabic}")):
            status, resp, _ = self.req("GET", path)
            self.assertEqual((status, resp.getheader("Location")), (301, location), path)
        for path in ("//evil.example/", "/%2F%2Fevil.example/"):
            status, resp, _ = self.req("GET", path)
            self.assertNotEqual(status, 301, path)
            self.assertIsNone(resp.getheader("Location"))
        self.assertEqual(self.req("GET", "/admin/")[0], 200, "the admin keeps its trailing slash")
        status, _, html = self.page("/index.html")
        self.assertEqual(status, 200)
        self.assertIn('<body data-page="home">', html)


DEFAULT_ABOUT = ("الفخامة للأقمشة والستائر متجر في جدة للأقمشة والستائر وتفصيلها.\n\n"
                 "نفصّل الستائر حسب مقاس نافذتك، ونوفّر ستائر رول وشرائح معدنية وستائر كهربائية، إلى جانب أقمشة الستائر.\n\n"
                 "قبل بدء التفصيل نؤكد معك المقاسات ونوع القماش والسعر النهائي، لتكون الصورة واضحة قبل اعتماد الطلب.")


def quote_url(text: str) -> str:
    from urllib.parse import quote
    return quote(text, safe="")


class ContactTests(ServerCase):
    ENV = {"CONTACT_LIMIT_PER_10MIN": "1000"}  # many messages here; the limiter has its own class below
    VALID = {"name": "عميل التواصل", "phone": "0551234567", "message": "أريد الاستفسار عن ستائر رول لغرفة النوم", "consent": True}

    def post(self, **fields):
        return self.json("POST", "/api/contact", {**self.VALID, **fields})

    def stored(self) -> list[dict]:
        cookie, _ = self.login()
        status, data, _ = self.json("GET", "/api/admin/messages", cookie=cookie)
        self.assertEqual(status, 200, data)
        return data["messages"]

    def test_valid_message_is_stored_without_login_or_csrf(self):
        status, data, resp = self.post(email="client@example.com", page="/contact", name="  عميل التواصل  ")
        self.assertEqual((status, data), (201, {"ok": True}))
        self.assertIn("application/json", resp.getheader("Content-Type"))
        latest = self.stored()[0]
        self.assertEqual((latest["name"], latest["phone"], latest["email"], latest["page"], latest["status"], latest["handled_at"]),
                         ("عميل التواصل", "0551234567", "client@example.com", "/contact", "new", ""))
        self.assertTrue(latest["created_at"] and latest["consent_at"])
        # Arabic-Indic digits and a leading + are normalised; extra JSON fields are ignored
        self.assertEqual(self.post(phone="+٩٦٦ ٥٥ ١٢٣ ٤٥٦٧", status="handled", id=1)[0], 201)
        latest = self.stored()[0]
        self.assertEqual((latest["phone"], latest["status"]), ("+966551234567", "new"))
        # the visitor's text is stored verbatim and only returned as JSON (escaped by the admin UI)
        self.assertEqual(self.post(message="<img src=x onerror=alert(1)> سؤال")[0], 201)
        self.assertEqual(self.stored()[0]["message"], "<img src=x onerror=alert(1)> سؤال")

    def test_validation_errors_store_nothing(self):
        before = len(self.stored())
        bad = [dict(name="x"), dict(name="ن" * 81), dict(name=None), dict(phone="12345"), dict(phone="1" * 17), dict(phone=""),
               dict(phone=["0551234567"]), dict(email="not-an-email"), dict(email="a@b"), dict(email=("x" * 110) + "@example.com"),
               dict(message="مرحبا"[:4]), dict(message="س" * 2001), dict(message=""), dict(consent=False), dict(consent="yes"),
               dict(consent=None)]
        for fields in bad:
            status, data, _ = self.post(**fields)
            self.assertEqual(status, 400, fields)
            self.assertTrue(data.get("error"), fields)
        status, data, _ = self.json("POST", "/api/contact", {k: v for k, v in self.VALID.items() if k != "consent"})
        self.assertEqual(status, 400)
        self.assertIn("الخصوصية", data["error"])
        self.assertEqual(self.req("POST", "/api/contact", b"{not json")[0], 400)
        self.assertEqual(self.req("POST", "/api/contact", b"[1,2]")[0], 400)
        self.assertEqual(self.req("POST", "/api/contact", b"x" * (40 * 1024))[0], 413)
        self.assertEqual(len(self.stored()), before)
        # limits are inclusive
        self.assertEqual(self.post(name="ن" * 80, phone="1" * 16, message="س" * 2000, email=("x" * 108) + "@example.com")[0], 201)
        self.assertEqual(self.post(name="نو", phone="12345678", message="سؤال؟")[0], 201)

    def test_page_hint_is_cleaned(self):
        for sent, kept in (("/contact", "/contact"), ("javascript:alert(1)", ""), ("//evil.example", ""), ("https://x.y/", ""),
                           ("/product/%D8%B3%D8%AA%D8%A7%D8%B1%D8%A9", "/product/ستارة"), ("/" + "a" * 300, "/" + "a" * 119), ("/a b", ""), (5, "")):
            self.assertEqual(self.post(page=sent)[0], 201, sent)
            self.assertEqual(self.stored()[0]["page"], kept, sent)

    def test_honeypot_is_accepted_silently_and_dropped(self):
        before = len(self.stored())
        status, data, _ = self.json("POST", "/api/contact", {"name": "bot", "phone": "1", "message": "x", "website": "http://spam.example"})
        self.assertEqual((status, data), (201, {"ok": True}))
        status, data, _ = self.post(website="filled")
        self.assertEqual((status, data), (201, {"ok": True}))
        self.assertEqual(len(self.stored()), before)
        self.assertEqual(self.post(website="")[0], 201, "an empty honeypot is a person")
        self.assertEqual(len(self.stored()), before + 1)

    def test_form_post_without_javascript(self):
        from urllib.parse import urlencode
        form = {"name": "زائر بلا سكربت", "phone": "0551112222", "email": "", "message": "<b>سؤال</b> عن الأقمشة", "consent": "1",
                "website": "", "page": "/contact"}
        hdrs = {"Content-Type": "application/x-www-form-urlencoded"}
        status, resp, body = self.req("POST", "/api/contact", urlencode(form).encode(), headers=hdrs)
        html = body.decode()
        self.assertEqual(status, 200)
        self.assertIn("text/html", resp.getheader("Content-Type"))
        self.assertIn('data-page="contact"', html)
        self.assertRegex(html, r'<p id="contactStatus"[^>]*data-state="success">شكرًا لك')
        self.assertEqual(self.stored()[0]["message"], "<b>سؤال</b> عن الأقمشة")
        # an error keeps what the visitor typed (escaped) and explains the problem
        status, _, body = self.req("POST", "/api/contact", urlencode({**form, "consent": ""}).encode(), headers=hdrs)
        html = body.decode()
        self.assertEqual(status, 400)
        self.assertRegex(html, r'data-state="error">يلزم الموافقة على سياسة الخصوصية')
        self.assertIn(">&lt;b&gt;سؤال&lt;/b&gt; عن الأقمشة</textarea>", html)
        self.assertIn('value="زائر بلا سكربت"', html)
        self.assertNotIn("<b>سؤال</b>", html)
        self.assertIn('content="noindex,nofollow"', html)


class ContactRateLimitTests(ServerCase):
    def test_accepted_messages_are_limited_per_client(self):
        body = {"name": "عميل", "phone": "0551234567", "message": "رسالة تجريبية", "consent": True}
        codes = [self.json("POST", "/api/contact", body)[0] for _ in range(6)]
        self.assertEqual(codes, [201] * 5 + [429])
        status, data, resp = self.json("POST", "/api/contact", body)
        self.assertEqual(status, 429)
        self.assertEqual(resp.getheader("Retry-After"), "600")
        self.assertTrue(data["error"])
        self.assertEqual(self.json("POST", "/api/contact", {**body, "phone": "1"})[0], 400, "invalid input is still explained")
        self.assertEqual(self.json("POST", "/api/contact", {**body, "website": "x"})[0], 201, "bots never learn about the limit")


class AdminMessagesTests(ServerCase):
    ENV = {"CONTACT_LIMIT_PER_10MIN": "1000"}

    def send(self, name: str):
        status, data, _ = self.json("POST", "/api/contact", {"name": name, "phone": "0551234567", "message": f"رسالة من {name}", "consent": True})
        self.assertEqual(status, 201, data)

    def test_login_and_csrf_are_required(self):
        self.send("عميل أول")
        self.assertEqual(self.req("GET", "/api/admin/messages")[0], 401)
        self.assertEqual(self.json("PATCH", "/api/admin/messages/1", {"status": "handled"})[0], 401)
        self.assertEqual(self.json("DELETE", "/api/admin/messages/1")[0], 401)
        cookie, csrf = self.login()
        self.assertEqual(self.json("PATCH", "/api/admin/messages/1", {"status": "handled"}, cookie=cookie)[0], 403)
        self.assertEqual(self.json("PATCH", "/api/admin/messages/1", {"status": "handled"}, cookie=cookie, csrf="wrong")[0], 403)
        self.assertEqual(self.json("DELETE", "/api/admin/messages/1", cookie=cookie)[0], 403)
        self.assertEqual(self.json("DELETE", "/api/admin/messages/1", cookie=cookie, csrf="wrong")[0], 403)
        _, resp, _ = self.req("GET", "/api/admin/messages", cookie=cookie)
        self.assertEqual(resp.getheader("Cache-Control"), "no-store")
        self.assertIn("noindex", resp.getheader("X-Robots-Tag"))

    def test_list_filter_status_counts_delete_and_audit(self):
        cookie, csrf = self.login()
        kw = dict(cookie=cookie, csrf=csrf)
        start = self.json("GET", "/api/admin/messages", cookie=cookie)[1]["counts"]
        for name in ("سارة", "خالد", "منى"):
            self.send(name)
        status, data, _ = self.json("GET", "/api/admin/messages", cookie=cookie)
        self.assertEqual(status, 200)
        ids = [m["id"] for m in data["messages"]]
        self.assertEqual(ids, sorted(ids, reverse=True), "newest first")
        self.assertEqual(data["messages"][0]["name"], "منى")
        self.assertEqual(set(data["messages"][0]), {"id", "name", "phone", "email", "message", "page", "status", "status_label",
                                                     "created_at", "handled_at", "consent_at"})
        self.assertEqual(data["counts"], {"new": start["new"] + 3, "handled": start["handled"], "total": start["total"] + 3})
        target = data["messages"][1]["id"]
        status, changed, _ = self.json("PATCH", f"/api/admin/messages/{target}", {"status": "handled"}, **kw)
        self.assertEqual(status, 200, changed)
        self.assertTrue(changed["ok"])
        self.assertEqual((changed["message"]["id"], changed["message"]["status"]), (target, "handled"))
        self.assertTrue(changed["message"]["handled_at"])
        handled = self.json("GET", "/api/admin/messages?status=handled", cookie=cookie)[1]
        self.assertEqual([m["id"] for m in handled["messages"]], [target] + [m["id"] for m in handled["messages"][1:]])
        self.assertTrue(all(m["status"] == "handled" for m in handled["messages"]))
        fresh = self.json("GET", "/api/admin/messages?status=new", cookie=cookie)[1]
        self.assertNotIn(target, [m["id"] for m in fresh["messages"]])
        self.assertTrue(all(m["status"] == "new" for m in fresh["messages"]))
        self.assertEqual(fresh["counts"], {"new": start["new"] + 2, "handled": start["handled"] + 1, "total": start["total"] + 3})
        self.assertEqual(len(self.json("GET", "/api/admin/messages?status=bogus", cookie=cookie)[1]["messages"]), start["total"] + 3)
        _, dash, _ = self.json("GET", "/api/admin/dashboard", cookie=cookie)
        self.assertEqual(dash["new_messages"], start["new"] + 2)
        # back to new clears handled_at; bad status and unknown ids are refused
        status, changed, _ = self.json("PATCH", f"/api/admin/messages/{target}", {"status": "new"}, **kw)
        self.assertEqual((status, changed["message"]["status"], changed["message"]["handled_at"]), (200, "new", ""))
        self.assertEqual(self.json("PATCH", f"/api/admin/messages/{target}", {"status": "archived"}, **kw)[0], 400)
        self.assertEqual(self.json("PATCH", "/api/admin/messages/999999", {"status": "handled"}, **kw)[0], 404)
        self.assertEqual(self.json("DELETE", f"/api/admin/messages/{target}", **kw)[0], 200)
        self.assertEqual(self.json("DELETE", f"/api/admin/messages/{target}", **kw)[0], 404)
        self.assertNotIn(target, [m["id"] for m in self.json("GET", "/api/admin/messages", cookie=cookie)[1]["messages"]])
        actions = [e["action"] for e in self.json("GET", "/api/admin/audit", cookie=cookie)[1]["entries"]]
        self.assertIn("message_status", actions)
        self.assertIn("message_delete", actions)


class SettingsAndCategoryContentTests(ServerCase):
    def test_about_settings_round_trip_and_limits(self):
        cookie, csrf = self.login()
        kw = dict(cookie=cookie, csrf=csrf)
        _, store, _ = self.json("GET", "/api/store")
        self.assertEqual((store["about_title"], store["opening_hours"]), ("من نحن", ""))
        settings = self.json("GET", "/api/admin/settings", cookie=cookie)[1]["settings"]
        self.assertEqual(settings["about_body"], DEFAULT_ABOUT)
        # the default text uses known facts only: no address, no years or counts
        self.assertIn("جدة", settings["about_body"])
        self.assertNotIn("مشرفة", settings["about_body"])
        self.assertIsNone(re.search(r"[0-9٠-٩]", settings["about_body"]))
        status, data, _ = self.json("PUT", "/api/admin/settings", {"about_title": "عن المتجر", "about_body": "فقرة أولى\n\nفقرة ثانية\nسطر",
                                                                   "opening_hours": "يوميًا"}, **kw)
        self.assertEqual(status, 200, data)
        _, store, _ = self.json("GET", "/api/store")
        self.assertEqual((store["about_title"], store["opening_hours"]), ("عن المتجر", "يوميًا"))
        html = self.req("GET", "/about")[2].decode()
        self.assertIn('<h1 id="pageTitle">عن المتجر</h1>', html)
        self.assertIn("<title>عن المتجر | ", html)
        self.assertIn("<p>فقرة أولى</p><p>فقرة ثانية<br>سطر</p>", main_of(html))
        self.assertIn("أوقات العمل", main_of(html))
        self.json("PUT", "/api/admin/settings", {"about_title": "ع" * 200, "about_body": "ب" * 4000, "opening_hours": "و" * 400}, **kw)
        settings = self.json("GET", "/api/admin/settings", cookie=cookie)[1]["settings"]
        self.assertEqual((len(settings["about_title"]), len(settings["about_body"]), len(settings["opening_hours"])), (120, 3000, 300))
        # an empty title falls back to the default heading
        self.json("PUT", "/api/admin/settings", {"about_title": "", "about_body": DEFAULT_ABOUT, "opening_hours": ""}, **kw)
        self.assertEqual(self.json("GET", "/api/store")[1]["about_title"], "من نحن")
        self.assertIn('<h1 id="pageTitle">من نحن</h1>', self.req("GET", "/about")[2].decode())
        self.assertEqual(self.json("GET", "/api/store")[1]["phone"], "+966 57 648 6491", "contact details untouched")

    def test_category_description_round_trip(self):
        cookie, csrf = self.login()
        kw = dict(cookie=cookie, csrf=csrf)
        status, data, _ = self.json("POST", "/api/admin/categories", {"name": "ستائر المجالس", "description": "  " + "و" * 450}, **kw)
        self.assertEqual(status, 201, data)
        slug = data["slug"]
        self.assertEqual(data["description"], "و" * 400)
        from urllib.parse import quote
        url = "/api/admin/categories/" + quote(slug)
        try:
            pub = {c["slug"]: c for c in self.json("GET", "/api/categories")[1]["categories"]}
            self.assertEqual(pub[slug]["description"], "و" * 400)
            self.assertTrue(all("description" in c for c in pub.values()))
            status, data, _ = self.json("PUT", url, {"name": "ستائر المجالس", "description": "ستائر تُفصَّل للمجالس.\nسطر ثانٍ"}, **kw)
            self.assertEqual((status, data["description"]), (200, "ستائر تُفصَّل للمجالس.\nسطر ثانٍ"))
            admin = {c["slug"]: c for c in self.json("GET", "/api/admin/categories", cookie=cookie)[1]["categories"]}
            self.assertEqual(admin[slug]["description"], "ستائر تُفصَّل للمجالس.\nسطر ثانٍ")
            status, data, _ = self.json("PUT", url, {"name": "ستائر المجالس", "active": True}, **kw)
            self.assertEqual(data["description"], "ستائر تُفصَّل للمجالس.\nسطر ثانٍ", "a client that does not send it keeps it")
            html = self.req("GET", "/category/" + quote(slug))[2].decode()
            self.assertIn('<p class="page-hero-intro">ستائر تُفصَّل للمجالس.<br>سطر ثانٍ</p>', html)
            self.assertIn('content="ستائر تُفصَّل للمجالس. سطر ثانٍ"', html, "meta description from the category text")
            status, data, _ = self.json("PUT", url, {"name": "ستائر المجالس", "description": ""}, **kw)
            self.assertEqual(data["description"], "")
        finally:
            self.json("DELETE", url, **kw)


class ShellUnitTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(ROOT))

    def test_fill_shell_swaps_main_body_styles_and_marks_the_nav(self):
        import pages
        template = ('<html><head><title>t</title></head><body data-page="home" class="x"><nav class="main-nav" id="mainNav">'
                    '<a href="/">ر</a><a href="/products">م</a><a href="/about">ع</a></nav><input id="searchInput" type="search">'
                    '<main id="main"><!--main:start-->HOME<!--main:end--></main></body></html>')
        out = pages.fill_shell(template, "<p>X</p>", "category", {"category": 'a"b'}, nav_path="/products", search='q"<')
        self.assertIn('<body data-page="category" data-category="a&quot;b">', out)
        self.assertIn("<!--main:start-->\n<p>X</p>\n<!--main:end-->", out)
        self.assertNotIn("HOME", out)
        self.assertIn('<a aria-current="page" href="/products">', out)
        self.assertEqual(out.split("<body")[1].count("aria-current"), 1)
        self.assertIn('<style id="pageStyles">', out.split("</head>")[0])
        self.assertIn('<input id="searchInput" value="q&quot;&lt;" type="search">', out)
        with self.assertRaises(RuntimeError):
            pages.fill_shell("<html><body></body></html>", "x", "faq")

    def test_home_faq_and_card_helpers(self):
        import pages
        template = (ROOT / "index.html").read_text(encoding="utf-8")
        out = pages.fill_home_faq(template)
        self.assertEqual(out.split("<!--faq:start-->")[1].split("<!--faq:end-->")[0].count("<details>"), len(pages.FAQ))
        self.assertEqual(pages.arabic_number(1450), "١٬٤٥٠")
        self.assertEqual(pages.price_html({"price": 0}), '<span class="on-request">السعر حسب الطلب</span>')
        self.assertEqual(pages.web_url("javascript:alert(1)"), "")
        self.assertEqual(pages.web_url("https://maps.example/x"), "https://maps.example/x")
        self.assertEqual(pages.whatsapp_href(""), "")
        self.assertEqual(pages.tel_href("+966 57 648 6491"), "tel:+966576486491")
        card = pages.product_card({"id": 7, "name": "<b>n</b>", "slug": "س ل", "category_name": "ق", "price": 20, "stock": 0,
                                   "thumb": "/assets/a.jpg", "alt": "", "badge": "x"})
        self.assertIn('<a class="product-link" href="/product/%D8%B3%20%D9%84">&lt;b&gt;n&lt;/b&gt;</a>', card)
        self.assertIn('<span class="product-badge">نفد المخزون</span>', card)
        self.assertIn('<button class="quick-add" data-quick="7">عرض التفاصيل</button>', card)
        self.assertIn("٢٠ <small>ر.س</small>", card)

    def test_seo_helpers(self):
        import seo
        self.assertTrue(seo.clip("كلمة " * 80).endswith("…"))
        self.assertLessEqual(len(seo.clip("كلمة " * 80)), 156)
        xml = seo.sitemap_xml("https://s.example", [], ["/privacy"], seo.MAIN_PAGES, ["curtains", "ستائر"])
        for loc in ("https://s.example/products", "https://s.example/faq", "https://s.example/category/curtains",
                    "https://s.example/category/%D8%B3%D8%AA%D8%A7%D8%A6%D8%B1", "https://s.example/privacy"):
            self.assertIn(f"<loc>{loc}</loc>", xml)
        crumbs = seo.breadcrumb_schema("https://s.example", [("الرئيسية", "/"), ("الأقسام", "/categories")])
        self.assertEqual([i["position"] for i in crumbs["itemListElement"]], [1, 2])
        title, description = seo.page_meta("category", {"store_name": "S"}, name="ق", description="", count=3)
        self.assertEqual(title, "ق | S")
        self.assertIn("3 منتج", description)


class SeoUnitTests(unittest.TestCase):
    def test_json_ld_cannot_break_out_of_the_script_tag(self):
        sys.path.insert(0, str(ROOT))
        import seo
        text = seo.jsonld({"name": "</script><script>alert(1)</script>", "x": "<!--"})
        self.assertNotIn("<", text)
        self.assertEqual(json.loads(text)["name"], "</script><script>alert(1)</script>")

    def test_meta_values_are_escaped(self):
        sys.path.insert(0, str(ROOT))
        import seo
        template = (ROOT / "index.html").read_text(encoding="utf-8")
        out = seo.render_page(template, title='A "quoted" <b>', description="x' onerror='y", robots="noindex", canonical="", og_url="",
                              og_image="", og_type="website", schemas=[{"@type": "Store"}], site_name="S")
        self.assertIn("&lt;b&gt;", out)
        self.assertIn("&quot;quoted&quot;", out)
        self.assertNotIn("onerror='y", out.split("</head>")[0].replace("&#x27;", ""))


class LegacyDataMigrationTests(unittest.TestCase):
    """A database created by the original build stores 'assets/x.jpg'; it must be fixed up on start."""

    def test_relative_image_paths_are_migrated(self):
        import sqlite3
        data_dir = tempfile.mkdtemp(prefix="fakhama-test-")
        try:
            # Let the server create the schema, then rewrite paths the way the old build stored them.
            ServerCase.start_server.__func__(ServerCase, data_dir=data_dir, env={"ADMIN_PASSWORD": "Z" + secrets.token_urlsafe(16)})
            ServerCase.stop_server.__func__(ServerCase, remove_data=False)
            conn = sqlite3.connect(Path(data_dir) / "store.db")
            conn.execute("UPDATE products SET image=substr(image,2) WHERE image LIKE '/assets/%'")
            conn.commit()
            self.assertTrue(conn.execute("SELECT image FROM products LIMIT 1").fetchone()[0].startswith("assets/"))
            conn.close()
            ServerCase.start_server.__func__(ServerCase, data_dir=data_dir, env={"ADMIN_PASSWORD": "Z" + secrets.token_urlsafe(16)})
            conn = sqlite3.connect(Path(data_dir) / "store.db")
            images = [r[0] for r in conn.execute("SELECT image FROM products")]
            conn.close()
            self.assertTrue(images and all(i.startswith("/assets/") for i in images), images)
        finally:
            ServerCase.stop_server.__func__(ServerCase)

    def test_database_without_messages_or_category_text_is_upgraded(self):
        import sqlite3
        data_dir = tempfile.mkdtemp(prefix="fakhama-test-")
        try:
            ServerCase.start_server.__func__(ServerCase, data_dir=data_dir, env={"ADMIN_PASSWORD": "Z" + secrets.token_urlsafe(16)})
            ServerCase.stop_server.__func__(ServerCase, remove_data=False)
            conn = sqlite3.connect(Path(data_dir) / "store.db")
            conn.execute("DROP TABLE messages")
            conn.execute("ALTER TABLE categories DROP COLUMN description")
            conn.execute("DELETE FROM settings WHERE key IN ('about_title','about_body','opening_hours')")
            conn.commit()
            conn.close()
            ServerCase.start_server.__func__(ServerCase, data_dir=data_dir, env={"ADMIN_PASSWORD": "Z" + secrets.token_urlsafe(16)})
            conn = sqlite3.connect(Path(data_dir) / "store.db")
            self.assertIn("description", [r[1] for r in conn.execute("PRAGMA table_info(categories)")])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT value FROM settings WHERE key='about_title'").fetchone()[0], "من نحن")
            conn.close()
            conn = http.client.HTTPConnection("127.0.0.1", ServerCase.port, timeout=10)
            conn.request("GET", "/about")
            self.assertEqual(conn.getresponse().status, 200)
            conn.close()
        finally:
            ServerCase.stop_server.__func__(ServerCase)



# -- first-party statistics, Google settings, backups --------------------------------------------------------------
BROWSER_UA = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Mobile Safari/537.36"
SHOP_TZ = __import__("datetime").timezone(__import__("datetime").timedelta(hours=3))


def shop_day(offset: int = 0) -> str:
    """The shop's calendar day (Jeddah, UTC+3) `offset` days ago, as stored in page_views.day."""
    from datetime import datetime, timedelta
    return (datetime.now(SHOP_TZ).date() - timedelta(days=offset)).isoformat()


def db_rows(data_dir: str, sql: str, args=()) -> list[tuple]:
    import sqlite3
    conn = sqlite3.connect(Path(data_dir) / "store.db", timeout=20)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def db_exec(data_dir: str, sql: str, rows: list[tuple] | None = None) -> None:
    import sqlite3
    conn = sqlite3.connect(Path(data_dir) / "store.db", timeout=20)
    try:
        if rows is None:
            conn.execute(sql)
        else:
            conn.executemany(sql, rows)
        conn.commit()
    finally:
        conn.close()


class AnalyticsCountingTests(ServerCase):
    ENV = {"CONTACT_LIMIT_PER_10MIN": "1000"}

    def counts(self) -> tuple[dict, dict]:
        pages = dict(db_rows(self.data_dir, "SELECT path, SUM(views) FROM page_views GROUP BY path"))
        refs = dict(db_rows(self.data_dir, "SELECT host, SUM(views) FROM referrers GROUP BY host"))
        return pages, refs

    def visit(self, path, method="GET", ua=BROWSER_UA, **headers):
        hdrs = dict(headers)
        if ua is not None:
            hdrs["User-Agent"] = ua
        return self.req(method, path, headers=hdrs)[0]

    @staticmethod
    def delta(before: dict, after: dict) -> dict:
        return {k: after.get(k, 0) - before.get(k, 0) for k in set(before) | set(after) if after.get(k, 0) != before.get(k, 0)}

    def test_successful_public_page_views_are_counted_once_per_page(self):
        before, _ = self.counts()
        visits = {"/": "/", "/index.html": "/", "/products": "/products", "/products?q=%D8%B3%D8%AA%D8%A7%D8%B1%D8%A9": "/products",
                  "/categories": "/categories", "/category/curtains": "/category/curtains", "/product/wavy-03": "/product/wavy-03",
                  "/about": "/about", "/contact": "/contact", "/faq": "/faq", "/privacy": "/privacy", "/terms": "/terms"}
        expected: dict[str, int] = {}
        for url, stored in visits.items():
            self.assertEqual(self.visit(url), 200, url)
            expected[stored] = expected.get(stored, 0) + 1
        after, _ = self.counts()
        self.assertEqual(self.delta(before, after), expected)
        # the search term never reaches the statistics (only the page's own path is stored)
        self.assertLessEqual({r[0] for r in db_rows(self.data_dir, "SELECT path FROM page_views")}, set(visits.values()))
        self.assertEqual({r[0] for r in db_rows(self.data_dir, "SELECT day FROM page_views")}, {shop_day()})

    def test_bots_prefetch_head_errors_assets_api_and_the_admin_are_not_counted(self):
        cookie, _ = self.login()
        before, refs_before = self.counts()
        skipped = [
            ("/", "HEAD", BROWSER_UA, {}),
            ("/", "GET", BROWSER_UA, {"Sec-Purpose": "prefetch"}),
            ("/products", "GET", BROWSER_UA, {"Sec-Purpose": "prefetch;prerender"}),
            ("/", "GET", BROWSER_UA, {"Purpose": "prefetch"}),
            ("/", "GET", BROWSER_UA, {"X-Moz": "prefetch"}),
            ("/", "GET", "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)", {}),
            ("/", "GET", "Mozilla/5.0 (compatible; bingbot/2.0)", {}),
            ("/", "GET", "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)", {}),
            ("/product/wavy-03", "GET", "WhatsApp/2.23.20.0 A", {}),
            ("/", "GET", "Mozilla/5.0 (compatible; Yahoo! Slurp)", {}),
            ("/", "GET", "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; Google Page Speed Insights) Chrome/120 Preview", {}),
            ("/", "GET", "SomeCrawler/1.0", {}),
            ("/", "GET", "Baiduspider", {}),
            ("/", "GET", None, {}),                      # no user agent at all
            ("/", "GET", BROWSER_UA, {"Cookie": cookie}),  # the signed-in admin
            ("/products", "GET", BROWSER_UA, {"Cookie": "fakhama_admin_flag=1"}),
            ("/no-such-page", "GET", BROWSER_UA, {}),
            ("/product/no-such-product", "GET", BROWSER_UA, {}),
            ("/about/", "GET", BROWSER_UA, {}),         # 301 to /about
            ("/api/products", "GET", BROWSER_UA, {}),
            ("/api/store", "GET", BROWSER_UA, {}),
            ("/assets/wavy-11.jpg", "GET", BROWSER_UA, {}),
            ("/sw.js", "GET", BROWSER_UA, {}),
            ("/robots.txt", "GET", BROWSER_UA, {}),
            ("/offline.html", "GET", BROWSER_UA, {}),
            ("/admin/", "GET", BROWSER_UA, {}),
            ("/admin/", "GET", BROWSER_UA, {"Cookie": cookie}),
        ]
        for path, method, ua, headers in skipped:
            self.visit(path, method, ua, **headers)
        # the contact form posted without JavaScript answers with the contact page, but a POST is not a page view
        body = "name=%D8%B3%D8%A7%D8%B1%D8%A9&phone=0551234567&message=%D8%A7%D8%B3%D8%AA%D9%81%D8%B3%D8%A7%D8%B1+%D8%B9%D9%86+%D8%A7%D9%84%D8%B3%D8%AA%D8%A7%D8%A6%D8%B1&consent=on"
        status, _, _ = self.req("POST", "/api/contact", body.encode(), headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": BROWSER_UA})
        self.assertEqual(status, 200)
        after, refs_after = self.counts()
        self.assertEqual(self.delta(before, after), {}, "nothing above is a customer page view")
        self.assertEqual(self.delta(refs_before, refs_after), {})

    def test_referrers_store_only_another_site_host_name(self):
        _, before = self.counts()
        host = f"127.0.0.1:{self.port}"
        cases = [
            ({}, "direct"),
            ({"Referer": "https://www.google.com/search?q=private+words&client=x"}, "google.com"),
            ({"Referer": "https://l.instagram.com/?u=https%3A%2F%2Fexample"}, "l.instagram.com"),
            ({"Referer": "android-app://com.google.android.gm/"}, "com.google.android.gm"),
            ({"Referer": "not a url at all"}, "direct"),
            ({"Referer": f"http://{host}/products"}, None),                                  # moving between our own pages
            ({"Referer": "https://shop.example.com/about", "Host": "shop.example.com"}, None),
            ({"Referer": "https://www.shop.example.com/", "Host": "shop.example.com"}, None),
        ]
        expected: dict[str, int] = {}
        for headers, stored in cases:
            self.assertEqual(self.visit("/about", **headers), 200, headers)
            if stored:
                expected[stored] = expected.get(stored, 0) + 1
        _, after = self.counts()
        self.assertEqual(self.delta(before, after), expected)
        stored_hosts = {r[0] for r in db_rows(self.data_dir, "SELECT host FROM referrers")}
        self.assertFalse([h for h in stored_hosts if "/" in h or "?" in h or "private" in h], stored_hosts)

    def test_forged_referrers_cannot_grow_the_table_without_bound(self):
        today = shop_day()
        db_exec(self.data_dir, "INSERT OR IGNORE INTO referrers(day,host,views) VALUES(?,?,1)", [(today, f"filler{i}.example") for i in range(200)])
        _, before = self.counts()
        self.assertEqual(self.visit("/faq", Referer="https://brand-new-site.example/x"), 200)
        self.assertEqual(self.visit("/faq", Referer="https://filler7.example/"), 200)  # a host already counted today
        _, after = self.counts()
        self.assertEqual(self.delta(before, after), {"other": 1, "filler7.example": 1})
        self.assertFalse(db_rows(self.data_dir, "SELECT 1 FROM referrers WHERE host='brand-new-site.example'"))
        db_exec(self.data_dir, "DELETE FROM referrers WHERE host LIKE 'filler%' OR host='other'")  # other tests use today's rows

    def test_no_visitor_data_is_stored(self):
        self.visit("/", Referer="https://www.google.com/", Cookie="tracking=abc")
        tables = {r[0] for r in db_rows(self.data_dir, "SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ("page_views", "referrers"):
            self.assertIn(table, tables)
            columns = [r[1] for r in db_rows(self.data_dir, f"PRAGMA table_info({table})")]
            self.assertEqual(columns, ["day", table == "page_views" and "path" or "host", "views"])
        _, resp, _ = self.req("GET", "/", headers={"User-Agent": BROWSER_UA})
        self.assertIsNone(resp.getheader("Set-Cookie"), "counting a visit sets no cookie")


class AnalyticsReportTests(ServerCase):
    ENV = {"CONTACT_LIMIT_PER_10MIN": "1000"}

    def test_admin_only_and_period_validation(self):
        self.assertEqual(self.json("GET", "/api/admin/analytics")[0], 401)
        self.assertEqual(self.json("GET", "/api/admin/analytics?days=30")[0], 401)
        cookie, _ = self.login()
        for bad in ("5", "abc", "0", "-7", "365", "30.0"):
            status, data, _ = self.json("GET", f"/api/admin/analytics?days={bad}", cookie=cookie)
            self.assertEqual(status, 400, bad)
            self.assertIn("الفترة", data["error"])
        for days in (7, 30, 90):
            status, data, resp = self.json("GET", f"/api/admin/analytics?days={days}", cookie=cookie)
            self.assertEqual(status, 200, data)
            self.assertEqual(resp.getheader("Cache-Control"), "no-store")
            self.assertEqual(len(data["daily"]), days)
            self.assertEqual(data["daily"][-1]["date"], shop_day())
            self.assertEqual([d["date"] for d in data["daily"]], [shop_day(i) for i in range(days - 1, -1, -1)])
            for key in ("totals", "top_pages", "top_products", "referrers", "orders_last30", "contact_last30"):
                self.assertIn(key, data)
            self.assertEqual(set(data["totals"]), {"today", "last7", "last30"})

    def test_aggregation_over_periods(self):
        from datetime import datetime, timedelta, timezone
        cookie, _ = self.login()
        db_exec(self.data_dir, "DELETE FROM page_views")
        db_exec(self.data_dir, "DELETE FROM referrers")
        db_exec(self.data_dir, "INSERT INTO page_views(day,path,views) VALUES(?,?,?)", [
            (shop_day(0), "/", 5), (shop_day(3), "/products", 7), (shop_day(3), "/", 1), (shop_day(10), "/product/wavy-03", 4),
            (shop_day(12), "/product/%D9%85%D8%AD%D8%B0%D9%88%D9%81", 2), (shop_day(40), "/about", 9), (shop_day(100), "/faq", 50),
            (shop_day(-1), "/", 99),  # a future day (clock change) is not part of any period
        ])
        db_exec(self.data_dir, "INSERT INTO referrers(day,host,views) VALUES(?,?,?)", [
            (shop_day(0), "google.com", 3), (shop_day(5), "google.com", 1), (shop_day(10), "instagram.com", 2), (shop_day(40), "bing.com", 6)])
        # orders and messages: two recent ones each (through the API) and one older than 30 days (written directly)
        product_id = self.json("GET", "/api/products")[1]["products"][0]["id"]
        for _ in range(2):
            status, data, _ = self.json("POST", "/api/orders", {"name": "عميل", "phone": "0551234567", "city": "جدة", "consent": True,
                                                                "items": [{"product_id": product_id, "quantity": 1}]})
            self.assertEqual(status, 201, data)
            self.assertEqual(self.json("POST", "/api/contact", {"name": "عميل", "phone": "0551234567", "message": "استفسار عن الستائر", "consent": True})[0], 201)
        old = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat(timespec="seconds")
        db_exec(self.data_dir, "INSERT INTO orders(order_number,customer_name,phone,city,created_at,updated_at) VALUES('OLD-1','قديم','0500000000','جدة',?,?)", [(old, old)])
        db_exec(self.data_dir, "INSERT INTO messages(name,phone,message,consent_at,created_at) VALUES('قديم','0500000000','رسالة قديمة',?,?)", [(old, old)])

        get = lambda days: self.json("GET", f"/api/admin/analytics?days={days}", cookie=cookie)[1]  # noqa: E731
        week = get(7)
        self.assertEqual(week["totals"], {"today": 5, "last7": 13, "last30": 19})
        self.assertEqual((week["daily"][-1]["views"], week["daily"][-4]["views"], sum(d["views"] for d in week["daily"])), (5, 8, 13))
        self.assertEqual(week["top_pages"], [{"path": "/products", "views": 7}, {"path": "/", "views": 6}])
        self.assertEqual(week["top_products"], [])
        self.assertEqual(week["referrers"], [{"host": "google.com", "views": 4}])
        self.assertEqual((week["orders_last30"], week["contact_last30"]), (2, 2))

        month = get(30)
        self.assertEqual(month["totals"], week["totals"])
        self.assertEqual(month["top_pages"][:3], [{"path": "/products", "views": 7}, {"path": "/", "views": 6}, {"path": "/product/wavy-03", "views": 4}])
        self.assertEqual(month["top_products"][0], {"slug": "wavy-03", "name": "ستائر ويفي تفصيل حسب الطلب — موديل 3", "views": 4})
        self.assertEqual(month["top_products"][1], {"slug": "محذوف", "name": "", "views": 2}, "a deleted product keeps its slug, without a name")
        self.assertEqual(month["referrers"], [{"host": "google.com", "views": 4}, {"host": "instagram.com", "views": 2}])

        quarter = get(90)
        self.assertEqual(quarter["top_pages"][0], {"path": "/about", "views": 9})
        self.assertNotIn("/faq", [p["path"] for p in quarter["top_pages"]], "100 days ago is outside 90 days")
        self.assertEqual(quarter["referrers"][0], {"host": "bing.com", "views": 6})
        self.assertEqual(sum(d["views"] for d in quarter["daily"]), 28)
        self.assertEqual(quarter["totals"], week["totals"])

    def test_admin_page_has_the_statistics_screen(self):
        _, _, body = self.req("GET", "/admin/")
        html = body.decode()
        self.assertIn('data-page="analytics"', html)
        self.assertIn("الإحصاءات", html)
        self.assertIn("/api/admin/analytics?days=", html)
        self.assertIn("احصاءات مجمعة بلا ملفات تعريف ارتباط ولا بيانات شخصية", html)


class AnalyticsPruneTests(unittest.TestCase):
    def test_statistics_older_than_400_days_are_pruned_at_startup(self):
        data_dir = tempfile.mkdtemp(prefix="fakhama-test-")
        env = {"ADMIN_PASSWORD": "Z" + secrets.token_urlsafe(16)}
        try:
            ServerCase.start_server.__func__(ServerCase, data_dir=data_dir, env=env)
            ServerCase.stop_server.__func__(ServerCase, remove_data=False)
            rows = [(shop_day(n), "/", n + 1) for n in (0, 1, 398, 399, 400, 401, 1000)]
            db_exec(data_dir, "INSERT INTO page_views(day,path,views) VALUES(?,?,?)", rows)
            db_exec(data_dir, "INSERT INTO referrers(day,host,views) VALUES(?,?,?)", [(d, "google.com", v) for d, _, v in rows])
            ServerCase.start_server.__func__(ServerCase, data_dir=data_dir, env=env)
            kept = sorted(r[0] for r in db_rows(data_dir, "SELECT day FROM page_views"))
            self.assertEqual(kept, sorted(shop_day(n) for n in (0, 1, 398, 399)))
            self.assertEqual(sorted(r[0] for r in db_rows(data_dir, "SELECT day FROM referrers")), kept)
        finally:
            ServerCase.stop_server.__func__(ServerCase)


def csp_of(resp) -> dict[str, str]:
    return {part.split(" ", 1)[0]: part.split(" ", 1)[1] if " " in part else "" for part in resp.getheader("Content-Security-Policy").split("; ")}


# what a page must not contain while no GA4 ID / verification token is set (the storefront script itself may mention them)
NO_GOOGLE_MARKUP = ('id="consentBanner"', 'id="cookieSettings"', '<meta name="google-site-verification"', "data-ga4-id",
                    'src="https://www.googletagmanager.com')
GA_HOSTS = ("https://*.google-analytics.com", "https://*.analytics.google.com", "https://*.googletagmanager.com")
STOREFRONT_PATHS = ("/", "/products", "/categories", "/category/curtains", "/product/wavy-03", "/about", "/contact", "/faq", "/no-such-page")


class GoogleSettingsTests(ServerCase):
    def put(self, body, cookie, csrf):
        return self.json("PUT", "/api/admin/settings", body, cookie=cookie, csrf=csrf)

    def test_settings_are_validated(self):
        cookie, csrf = self.login()
        for bad in ("UA-12345-1", "G-", "G-abc", "G-12", "G-<script>", "G-1234567890123456", "javascript:alert(1)", "G-AB12 CD", "AW-123456789"):
            status, data, _ = self.put({"ga4_id": bad}, cookie, csrf)
            self.assertEqual(status, 400, bad)
            self.assertIn("G-", data["error"])
        for bad in ("short", "has spaces inside it", '"><script>alert(1)</script>', "a" * 121, '<meta name="x" content="bad value!">'):
            self.assertEqual(self.put({"google_site_verification": bad}, cookie, csrf)[0], 400, bad)
        settings = self.json("GET", "/api/admin/settings", cookie=cookie)[1]["settings"]
        self.assertEqual((settings["ga4_id"], settings["google_site_verification"]), ("", ""), "rejected values are not stored")
        status, data, _ = self.put({"ga4_id": " g-ab12cd34ef ", "google_site_verification": '<meta name="google-site-verification" content="Abc_DEF-123456789xyz" />'}, cookie, csrf)
        self.assertEqual(status, 200, data)
        self.assertEqual((data["settings"]["ga4_id"], data["settings"]["google_site_verification"]), ("G-AB12CD34EF", "Abc_DEF-123456789xyz"))
        status, data, _ = self.put({"google_site_verification": "google-site-verification=TxtStyle_token-0123"}, cookie, csrf)
        self.assertEqual(data["settings"]["google_site_verification"], "TxtStyle_token-0123")
        # one invalid value rejects the whole update (nothing half-saved)
        self.assertEqual(self.put({"tagline": "لن تُحفظ", "ga4_id": "bad"}, cookie, csrf)[0], 400)
        self.assertNotEqual(self.json("GET", "/api/store")[1]["tagline"], "لن تُحفظ")
        status, data, _ = self.put({"ga4_id": "", "google_site_verification": ""}, cookie, csrf)
        self.assertEqual((data["settings"]["ga4_id"], data["settings"]["google_site_verification"]), ("", ""))
        self.assertEqual(self.json("PUT", "/api/admin/settings", {"ga4_id": "G-ABCD1234"})[0], 401)

    def test_nothing_changes_without_a_ga4_id(self):
        for path in STOREFRONT_PATHS + ("/privacy", "/terms", "/admin/", "/offline.html"):
            status, resp, body = self.req("GET", path, headers={"User-Agent": BROWSER_UA})
            nonce = re.search(r"'nonce-([^']+)'", resp.getheader("Content-Security-Policy")).group(1)
            self.assertEqual(resp.getheader("Content-Security-Policy"),
                             "default-src 'self'; img-src 'self' data:; style-src 'self' 'nonce-" + nonce + "'; style-src-attr 'unsafe-inline'; "
                             "script-src 'self' 'nonce-" + nonce + "'; connect-src 'self'; manifest-src 'self'; worker-src 'self'; "
                             "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'", path)
            html = body.decode()
            for absent in NO_GOOGLE_MARKUP:
                self.assertNotIn(absent, html, (path, absent))
        privacy = self.req("GET", "/privacy")[2].decode()
        self.assertNotIn("Google Analytics", privacy)
        self.assertIn("إحصاءات الزيارات", privacy, "the first-party statistics are disclosed")
        self.assertIn("لا نستخدم ملفات تعريف ارتباط للتتبع أو للإعلانات", privacy)

    def test_ga4_widens_the_csp_of_storefront_pages_only_and_adds_the_banner(self):
        cookie, csrf = self.login()
        self.assertEqual(self.put({"ga4_id": "G-TEST12345", "google_site_verification": "Verify_Token-1234567890"}, cookie, csrf)[0], 200)
        try:
            for path in STOREFRONT_PATHS:
                status, resp, body = self.req("GET", path, headers={"User-Agent": BROWSER_UA})
                self.assertEqual(status, 404 if path == "/no-such-page" else 200, path)
                csp = csp_of(resp)
                nonce = re.search(r"'nonce-([^']+)'", csp["script-src"]).group(1)
                self.assertEqual(csp["script-src"], f"'self' 'nonce-{nonce}' https://www.googletagmanager.com", path)
                self.assertEqual(csp["connect-src"], "'self' " + " ".join(GA_HOSTS), path)
                self.assertEqual(csp["img-src"], "'self' data: " + " ".join(GA_HOSTS), path)
                self.assertEqual(csp["default-src"], "'self'")
                self.assertNotIn("unsafe", csp["script-src"] + csp["style-src"])
                html = body.decode()
                head = html.split("</head>", 1)[0]
                self.assertEqual(head.count('<meta name="google-site-verification" content="Verify_Token-1234567890">'), 1, path)
                banner = re.search(r'<section class="consent-banner"[^>]*>.*?</section>', html, re.S)
                self.assertTrue(banner, path)
                tag = banner.group(0)
                for needle in ('role="region"', 'aria-labelledby="consentTitle"', 'data-ga4-id="G-TEST12345"', " hidden>", 'href="/privacy"',
                               '<button type="button" class="btn consent-btn" id="consentAccept">قبول</button>',
                               '<button type="button" class="btn consent-btn" id="consentReject">رفض</button>', "Google Analytics"):
                    self.assertIn(needle, tag, (path, needle))
                self.assertIn('id="cookieSettings" hidden>إعدادات ملفات التعريف</button>', html)
                # nothing from Google is in the markup itself: gtag.js is only created by the script after «قبول»
                self.assertNotIn("<script async src=\"https://www.googletagmanager.com", html)
                for script in re.findall(r"<script\b[^>]*>", html):
                    if "ld+json" not in script:
                        self.assertIn(f'nonce="{nonce}"', script)
                        self.assertNotIn("src=", script)
                self.assertFalse(handler_attributes(html), path)
            for path in ("/admin/", "/admin", "/admin/no-such-page", "/offline.html", "/privacy", "/terms"):
                _, resp, body = self.req("GET", path)
                self.assertNotIn("google", resp.getheader("Content-Security-Policy"), path)
                self.assertNotIn('id="consentBanner"', body.decode(), path)
            for path in ("/privacy", "/terms"):
                self.assertIn('<meta name="google-site-verification" content="Verify_Token-1234567890">', self.req("GET", path)[2].decode(), path)
            self.assertNotIn('<meta name="google-site-verification"', self.req("GET", "/admin/")[2].decode())
            privacy = self.req("GET", "/privacy")[2].decode()
            self.assertIn("Google Analytics (بموافقتك فقط)", privacy)
            for needle in ("https://policies.google.com/privacy", "إعدادات ملفات التعريف", "«رفض»", "_ga", "سحب الموافقة", "من يعالج البيانات", "ما الذي يُجمع"):
                self.assertIn(needle, privacy, needle)
            self.assertNotIn("Google Analytics", self.req("GET", "/terms")[2].decode())
            # the storefront script loads gtag.js only on «قبول», with the page nonce and Consent Mode defaults
            shell = (ROOT / "index.html").read_text(encoding="utf-8")
            for needle in ("fakhama-consent-v1", "s.nonce=PAGE_NONCE", "gtag('consent','default'", "analytics_storage:'denied'",
                           "gtag('consent','update',{analytics_storage:'granted'})", "if(state==='granted')loadAnalytics(id)"):
                self.assertIn(needle, shell, needle)
        finally:
            self.put({"ga4_id": "", "google_site_verification": ""}, cookie, csrf)
        _, resp, body = self.req("GET", "/")
        self.assertNotIn("google", resp.getheader("Content-Security-Policy"))
        for absent in NO_GOOGLE_MARKUP:
            self.assertNotIn(absent, body.decode(), absent)

    def test_a_bad_value_in_the_database_never_reaches_a_page(self):
        db_exec(self.data_dir, "UPDATE settings SET value='G-X\" onload=\"alert(1)' WHERE key='ga4_id'")
        db_exec(self.data_dir, "UPDATE settings SET value='\"><script>alert(1)</script>' WHERE key='google_site_verification'")
        try:
            _, resp, body = self.req("GET", "/")
            html = body.decode()
            self.assertNotIn("google", resp.getheader("Content-Security-Policy"))
            for absent in NO_GOOGLE_MARKUP:
                self.assertNotIn(absent, html, absent)
            self.assertNotIn("alert(1)", html)
        finally:
            db_exec(self.data_dir, "UPDATE settings SET value='' WHERE key IN ('ga4_id','google_site_verification')")


def zip_members(raw: bytes) -> dict[str, bytes]:
    import io
    import zipfile
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        return {name: zf.read(name) for name in zf.namelist()}


def table_counts_of(db_file: Path) -> dict[str, int]:
    import sqlite3
    conn = sqlite3.connect(db_file)
    try:
        names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return {n: conn.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0] for n in names}
    finally:
        conn.close()


class BackupDownloadTests(ServerCase):
    def test_backup_endpoints_need_the_admin(self):
        for path in ("/api/admin/backup.zip", "/api/admin/backups"):
            status, resp, body = self.req("GET", path)
            self.assertEqual(status, 401, path)
            self.assertNotIn(b"SQLite", body)
            self.assertNotIn("zip", resp.getheader("Content-Type"))
        self.assertEqual(self.req("GET", "/api/admin/backup.zip", headers={"Cookie": "fakhama_admin=forged"})[0], 401)

    def test_backup_zip_contents_headers_and_audit(self):
        import hashlib
        import sqlite3
        cookie, csrf = self.login()
        status, up, _ = self.json("POST", "/api/admin/upload", {"data": data_uri("image/png", PNG)}, cookie=cookie, csrf=csrf)
        self.assertEqual(status, 201, up)
        self.assertEqual(self.json("POST", "/api/contact", {"name": "عميل", "phone": "0551234567", "message": "سؤال عن الأقمشة", "consent": True})[0], 201)
        status, resp, raw = self.req("GET", "/api/admin/backup.zip", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(resp.getheader("Content-Type"), "application/zip")
        self.assertRegex(resp.getheader("Content-Disposition"), r'^attachment; filename="fakhama-backup-\d{8}-\d{6}\.zip"$')
        self.assertEqual(resp.getheader("Cache-Control"), "no-store")
        self.assertEqual(resp.getheader("X-Content-Type-Options"), "nosniff")
        self.assertIn("noindex", resp.getheader("X-Robots-Tag"))
        self.assertEqual(int(resp.getheader("Content-Length")), len(raw))
        members = zip_members(raw)
        upload_name = up["url"].rsplit("/", 1)[1]
        self.assertEqual(set(members), {"store.db", "manifest.json", f"uploads/{upload_name}"})
        self.assertEqual(members[f"uploads/{upload_name}"], PNG)
        manifest = json.loads(members["manifest.json"])
        self.assertEqual((manifest["app"], manifest["format"], manifest["uploads"]), ("fakhama-store", 1, 1))
        self.assertEqual(manifest["version"], self.json("GET", "/api/health")[1]["version"])
        self.assertRegex(manifest["created_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00$")
        self.assertEqual(manifest["store_db_sha256"], hashlib.sha256(members["store.db"]).hexdigest())
        self.assertEqual(members["store.db"][18:20], b"\x01\x01", "a self-contained file (rollback journal, not WAL)")
        work = Path(tempfile.mkdtemp(prefix="fakhama-test-"))
        try:
            (work / "store.db").write_bytes(members["store.db"])
            counts = table_counts_of(work / "store.db")
            self.assertEqual(manifest["tables"], counts)
            self.assertEqual((counts["products"], counts["messages"], counts["admins"]), (18, 1, 1))
            for table in ("page_views", "referrers", "audit_log", "settings", "orders", "order_items", "categories", "product_images"):
                self.assertIn(table, counts)
            conn = sqlite3.connect(work / "store.db")
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            conn.close()
        finally:
            shutil.rmtree(work, ignore_errors=True)
        _, audit, _ = self.json("GET", "/api/admin/audit", cookie=cookie)
        entry = next(e for e in audit["entries"] if e["action"] == "backup_download")
        self.assertEqual(entry["actor"], "admin")
        self.assertRegex(entry["detail"], r"^fakhama-backup-\d{8}-\d{6}\.zip: \d+ bytes, \d+ rows, 1 uploads$")
        self.assertFalse([p for p in Path(self.data_dir).iterdir() if p.name.startswith((".backup-", ".download-"))], "temporary files are removed")

    def test_backup_list_shows_archives_on_the_server(self):
        cookie, _ = self.login()
        status, data, _ = self.json("GET", "/api/admin/backups", cookie=cookie)
        self.assertEqual((status, data["backups"], data["auto_backup"], data["keep"]), (200, [], False, 14))
        folder = Path(self.data_dir) / "backups"
        folder.mkdir(exist_ok=True)
        for name in ("store-20260101-010101.db", "uploads-20260101-010101.tar.gz", "pre-restore-20260102-020202.zip", "notes.txt", "secret.db"):
            (folder / name).write_bytes(b"x" * 10)
        _, data, _ = self.json("GET", "/api/admin/backups", cookie=cookie)
        self.assertEqual(sorted(b["name"] for b in data["backups"]), ["pre-restore-20260102-020202.zip", "store-20260101-010101.db", "uploads-20260101-010101.tar.gz"])
        self.assertEqual({b["kind"] for b in data["backups"]}, {"database", "uploads", "pre-restore"})
        self.assertTrue(all(b["size"] == 10 and b["modified"] for b in data["backups"]))
        self.assertNotIn(self.data_dir, json.dumps(data), "no server paths are exposed")


def run_cli(data_dir: str, *args: str, port: int | None = None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in ("ADMIN_PASSWORD", "PORT", "HOST")}
    env.update({"DATA_DIR": data_dir, "PORT": str(port or free_port()), "HOST": "127.0.0.1"})
    return subprocess.run([sys.executable, "-I", str(ROOT / "app.py"), *args], capture_output=True, text=True, env=env, timeout=120, encoding="utf-8")


class SqlExportTests(ServerCase):
    def test_export_sql_has_every_table_but_no_admin_rows(self):
        import sqlite3
        self.assertEqual(self.json("POST", "/api/contact", {"name": "عميلة 'O\"Brien'", "phone": "0551234567", "message": "سطر أول\nسطر ثانٍ; DROP TABLE x;--", "consent": True})[0], 201)
        self.req("GET", "/about", headers={"User-Agent": BROWSER_UA, "Referer": "https://www.google.com/"})
        out_dir = Path(tempfile.mkdtemp(prefix="fakhama-test-"))
        try:
            result = run_cli(self.data_dir, "export-sql", str(out_dir / "dump.sql"))
            self.assertEqual(result.returncode, 0, result.stderr)
            dump = (out_dir / "dump.sql").read_text(encoding="utf-8")
            password_hash = db_rows(self.data_dir, "SELECT password_hash FROM admins")[0][0]
            self.assertNotIn(password_hash, dump)
            self.assertNotIn(password_hash.split("$")[1], dump)
            self.assertNotIn('INSERT INTO "admins"', dump)
            self.assertIn("CREATE TABLE IF NOT EXISTS admins", dump)
            for table in ("products", "categories", "settings", "messages", "page_views", "referrers", "product_images"):
                self.assertIn(f'INSERT INTO "{table}"', dump, table)
            # the dump loads into an empty database: same rows everywhere, no admin account
            imported = sqlite3.connect(":memory:")
            imported.executescript(dump)
            live = table_counts_of(Path(self.data_dir) / "store.db")
            for table, count in live.items():
                got = imported.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                self.assertEqual(got, 0 if table == "admins" else count, table)
            self.assertEqual(imported.execute("SELECT message FROM messages ORDER BY id DESC LIMIT 1").fetchone()[0], "سطر أول\nسطر ثانٍ; DROP TABLE x;--")
            self.assertEqual(imported.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            # stdout variant
            result = run_cli(self.data_dir, "export-sql")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('INSERT INTO "products"', result.stdout)
            self.assertNotIn(password_hash, result.stdout)
            # a server started on the imported database creates a fresh admin from ADMIN_PASSWORD
            fresh = out_dir / "fresh"
            fresh.mkdir()
            conn = sqlite3.connect(fresh / "store.db")
            conn.executescript(dump)
            conn.close()
            new_password = "N" + secrets.token_urlsafe(16)
            port = free_port()
            env = {k: v for k, v in os.environ.items() if k not in ("ADMIN_PASSWORD", "ADMIN_USERNAME")}
            env.update({"DATA_DIR": str(fresh), "PORT": str(port), "HOST": "127.0.0.1", "ADMIN_PASSWORD": new_password})
            proc = subprocess.Popen([sys.executable, "-I", str(ROOT / "app.py")], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                for _ in range(100):
                    try:
                        socket.create_connection(("127.0.0.1", port), timeout=0.3).close()
                        break
                    except OSError:
                        time.sleep(0.1)
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                conn.request("POST", "/api/admin/login", body=json.dumps({"username": "admin", "password": new_password}), headers={"Content-Type": "application/json"})
                self.assertEqual(conn.getresponse().status, 200)
                conn.close()
            finally:
                proc.terminate()
                proc.communicate(timeout=10)
        finally:
            shutil.rmtree(out_dir, ignore_errors=True)

    def test_export_sql_without_a_database_fails_cleanly(self):
        empty = tempfile.mkdtemp(prefix="fakhama-test-")
        try:
            result = run_cli(empty, "export-sql")
            self.assertEqual(result.returncode, 1)
            self.assertIn("لا توجد قاعدة بيانات", result.stderr)
            self.assertEqual(run_cli(empty, "export-sql", "a.sql", "b.sql").returncode, 2)
        finally:
            shutil.rmtree(empty, ignore_errors=True)


class RestoreTests(ServerCase):
    ENV = {"CONTACT_LIMIT_PER_10MIN": "1000"}

    def download_backup(self, cookie: str) -> bytes:
        status, _, raw = self.req("GET", "/api/admin/backup.zip", cookie=cookie)
        self.assertEqual(status, 200)
        return raw

    @staticmethod
    def rezip(members: dict[str, bytes], target: Path, extra: dict[str, bytes] | None = None) -> Path:
        import zipfile
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, data in {**members, **(extra or {})}.items():
                zf.writestr(name, data)
        return target

    def test_invalid_archives_are_refused_and_nothing_changes(self):
        import hashlib
        cookie, _ = self.login()
        good = zip_members(self.download_backup(cookie))
        work = Path(tempfile.mkdtemp(prefix="fakhama-test-"))
        try:
            manifest = json.loads(good["manifest.json"])
            tampered_counts = dict(manifest, tables={**manifest["tables"], "products": 999})
            foreign = dict(manifest, app="another-app")
            db = bytearray(good["store.db"])
            db[-1] ^= 0xFF
            cases = {
                "not-a-zip.zip": None,
                "no-manifest.zip": {"store.db": good["store.db"]},
                "traversal.zip": {**good, "../evil.txt": b"x"},
                "nested-traversal.zip": {**good, "uploads/../../app.py": b"x"},
                "unexpected.zip": {**good, "app.py": b"print(1)"},
                "counts.zip": {**good, "manifest.json": json.dumps(tampered_counts).encode()},
                "foreign.zip": {**good, "manifest.json": json.dumps(foreign).encode()},
                "checksum.zip": {**good, "store.db": bytes(db)},
                "not-sqlite.zip": {**good, "store.db": b"hello", "manifest.json": json.dumps(dict(manifest, store_db_sha256=hashlib.sha256(b"hello").hexdigest())).encode()},
                "uploads-count.zip": {**good, "uploads/extra.png": PNG},
            }
            before_db = hashlib.sha256((Path(self.data_dir) / "store.db").read_bytes()).hexdigest()
            products_before = len(self.json("GET", "/api/products")[1]["products"])
            for name, members in cases.items():
                archive = work / name
                if members is None:
                    archive.write_bytes(b"this is not a zip file")
                else:
                    self.rezip(members, archive)
                result = run_cli(self.data_dir, "restore", str(archive))
                self.assertEqual(result.returncode, 1, (name, result.stdout, result.stderr))
                self.assertIn("لم تتم الاستعادة ولم يتغير شيء", result.stderr, name)
            self.assertEqual(hashlib.sha256((Path(self.data_dir) / "store.db").read_bytes()).hexdigest(), before_db)
            self.assertEqual(len(self.json("GET", "/api/products")[1]["products"]), products_before)
            self.assertFalse((Path(self.data_dir) / "backups").exists(), "no safety copy for a refused archive")
            self.assertFalse((Path(self.data_dir) / "evil.txt").exists())
            # a valid archive is still refused while the server answers on the configured port
            self.rezip(good, work / "good.zip")
            result = run_cli(self.data_dir, "restore", str(work / "good.zip"), port=self.port)
            self.assertEqual(result.returncode, 1)
            self.assertIn("يعمل", result.stderr)
            self.assertEqual(run_cli(self.data_dir, "restore").returncode, 2)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def test_restore_round_trip(self):
        import zipfile
        cookie, csrf = self.login()
        kw = dict(cookie=cookie, csrf=csrf)
        status, up, _ = self.json("POST", "/api/admin/upload", {"data": data_uri("image/png", PNG)}, **kw)
        self.assertEqual(status, 201, up)
        self.assertEqual(self.json("PUT", "/api/admin/settings", {"tagline": "عبارة قبل الاستعادة"}, **kw)[0], 200)
        self.assertEqual(self.json("POST", "/api/contact", {"name": "عميل", "phone": "0551234567", "message": "رسالة محفوظة في النسخة", "consent": True})[0], 201)
        archive_dir = Path(tempfile.mkdtemp(prefix="fakhama-test-"))
        archive = archive_dir / "backup.zip"
        archive.write_bytes(self.download_backup(cookie))
        type(self).stop_server(remove_data=False)
        try:
            # the shop changes after the backup: data deleted, a photo lost, a stray file added
            db_exec(self.data_dir, "DELETE FROM messages")
            db_exec(self.data_dir, "UPDATE settings SET value='عبارة بعد النسخة' WHERE key='tagline'")
            db_exec(self.data_dir, "DELETE FROM product_images WHERE product_id IN (SELECT id FROM products WHERE slug='wavy-05')")
            db_exec(self.data_dir, "DELETE FROM products WHERE slug='wavy-05'")
            uploads = Path(self.data_dir) / "uploads"
            photo = uploads / up["url"].rsplit("/", 1)[1]
            photo.unlink()
            (uploads / "stray.png").write_bytes(PNG)
            result = run_cli(self.data_dir, "restore", str(archive))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("تمت الاستعادة", result.stdout)
            self.assertEqual(db_rows(self.data_dir, "SELECT value FROM settings WHERE key='tagline'")[0][0], "عبارة قبل الاستعادة")
            self.assertEqual(db_rows(self.data_dir, "SELECT message FROM messages"), [("رسالة محفوظة في النسخة",)])
            self.assertEqual(db_rows(self.data_dir, "SELECT COUNT(*) FROM products WHERE slug='wavy-05'")[0][0], 1)
            self.assertEqual(photo.read_bytes(), PNG)
            self.assertFalse((uploads / "stray.png").exists())
            self.assertFalse([p for p in Path(self.data_dir).iterdir() if p.name.startswith((".restore-", ".store.db"))], "no leftovers")
            # the state before the restore was saved first
            safety = list((Path(self.data_dir) / "backups").glob("pre-restore-*.zip"))
            self.assertEqual(len(safety), 1)
            with zipfile.ZipFile(safety[0]) as zf:
                saved = json.loads(zf.read("manifest.json"))
                self.assertIn("uploads/stray.png", zf.namelist())
            self.assertEqual(saved["tables"]["messages"], 0)
            # the restored store runs with the same admin password, data and photo
            type(self).start_server(data_dir=self.data_dir, env={"ADMIN_PASSWORD": "ignored-for-an-existing-db"})
            cookie, _ = self.login()
            self.assertEqual(self.json("GET", "/api/store")[1]["tagline"], "عبارة قبل الاستعادة")
            self.assertEqual(self.req("GET", up["url"])[0], 200)
            self.assertEqual(self.req("GET", "/product/wavy-05")[0], 200)
            _, data, _ = self.json("GET", "/api/admin/backups", cookie=cookie)
            self.assertEqual([b["kind"] for b in data["backups"]], ["pre-restore"])
        finally:
            shutil.rmtree(archive_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
