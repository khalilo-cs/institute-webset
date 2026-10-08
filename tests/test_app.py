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
                     "/api/admin/orders", "/api/admin/settings", "/api/admin/export/orders.csv"):
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
        status, order, _ = self.json("POST", "/api/orders", {"name": "عميل اختبار", "phone": "0551234567", "city": "جدة",
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
        body = {"name": "عميل اختبار", "phone": "0551234567", "city": "جدة", "note": "ملاحظة", "items": items, **fields}
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


class OrderRateLimitTests(ServerCase):
    def test_public_order_spam_is_limited(self):
        p = next(p for p in self.json("GET", "/api/products")[1]["products"] if p["stock"] >= 20)
        codes = []
        for _ in range(12):
            codes.append(self.json("POST", "/api/orders", {"name": "عميل", "phone": "0551234567", "city": "جدة",
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
