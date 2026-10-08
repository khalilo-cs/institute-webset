"""Server-side HTML for search engines and link previews: per-page meta tags, JSON-LD, sitemap, robots.

Crawlers and chat apps (WhatsApp, Telegram, X) read the first HTML response and usually do not run JavaScript,
so the title, description, canonical URL, Open Graph image and structured data are written here, not by the page.
Pure functions (no I/O) so they are unit-testable.
"""
from __future__ import annotations

import html
import json
import re
from urllib.parse import quote

SITE_NAME_FALLBACK = "الفخامة للأقمشة والستائر"


def esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def absolute(base: str, path: str) -> str:
    if not path:
        return ""
    return path if re.match(r"https?://", path) else f"{base}{path if path.startswith('/') else '/' + path}"


def jsonld(data: dict | list) -> str:
    """JSON safe to embed inside <script>: a literal '</' or '<!--' can never close/escape the element."""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")


def product_path(slug: str) -> str:
    return f"/product/{quote(slug, safe='')}"


def category_path(slug: str) -> str:
    return f"/category/{quote(slug, safe='')}"


HOME_LABEL = "الرئيسية"
# Store pages listed in the sitemap: (path, changefreq, priority).
MAIN_PAGES = [("/products", "daily", "0.9"), ("/categories", "weekly", "0.7"), ("/about", "monthly", "0.5"),
              ("/contact", "monthly", "0.5"), ("/faq", "monthly", "0.5")]


def clip(text: str, limit: int = 155) -> str:
    """One line of plain text for a meta description, cut on a word boundary."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip("،,.؛:-— ")
    return (cut or text[:limit]) + "…"


def page_meta(page: str, settings: dict, **ctx) -> tuple[str, str]:
    """(title, description) of a server-rendered page. Only store settings and catalogue data, no invented claims."""
    store = settings.get("store_name") or SITE_NAME_FALLBACK
    city = settings.get("city") or "جدة"
    if page == "products":
        return (f"كل المنتجات | {store}",
                f"تصفّح منتجات {store} في {city} بالصور والتفاصيل: ستائر تُفصَّل حسب المقاس وأقمشة الستائر، واطلب ما يناسب نافذتك.")
    if page == "categories":
        names = "، ".join(ctx.get("names") or [])
        return (f"الأقسام | {store}", clip(f"أقسام {store} في {city}" + (f": {names}." if names else ".")))
    if page == "category":
        name = ctx.get("name", "")
        desc = clip(ctx.get("description") or "") or f"{name} من {store} في {city}: {ctx.get('count', 0)} منتج بالصور والتفاصيل."
        return f"{name} | {store}", clip(desc)
    if page == "product":
        return f"{ctx.get('name', '')} | {store}", clip(ctx.get("description") or "") or settings.get("seo_description", "")
    if page == "about":
        title = (settings.get("about_title") or "").strip() or "من نحن"
        first = re.split(r"\n\s*\n", (settings.get("about_body") or "").strip())[0]
        return f"{title} | {store}", clip(first) or clip(f"{store} في {city}: تفصيل ستائر وأقمشة.")
    if page == "contact":
        return (f"تواصل معنا | {store}",
                f"تواصل مع {store} في {city}: الهاتف والعنوان والموقع على الخريطة، ونموذج لإرسال استفسارك عن الأقمشة والستائر والتفصيل.")
    if page == "faq":
        return (f"الأسئلة الشائعة | {store}",
                f"إجابات عن قياس النوافذ وأنواع الستائر والأسعار وطريقة الطلب لدى {store} في {city}.")
    return f"الصفحة غير موجودة | {store}", "الصفحة المطلوبة غير موجودة. ابحث في المنتجات أو تصفّح الأقسام."


def breadcrumb_schema(base: str, crumbs: list[tuple[str, str]]) -> dict:
    """crumbs = [(name, path), ...] from the home page down to the current page (matches the visible breadcrumbs)."""
    return {"@context": "https://schema.org", "@type": "BreadcrumbList",
            "itemListElement": [{"@type": "ListItem", "position": i, "name": name, "item": absolute(base, path)}
                                for i, (name, path) in enumerate(crumbs, 1)]}


def collection_schema(base: str, path: str, name: str, description: str, items: list[tuple[str, str]]) -> dict:
    """CollectionPage whose mainEntity is an ItemList of (name, path) entries (products or categories)."""
    return {"@context": "https://schema.org", "@type": "CollectionPage", "name": name, "description": description,
            "url": base + path, "inLanguage": "ar-SA",
            "mainEntity": {"@type": "ItemList", "numberOfItems": len(items),
                           "itemListElement": [{"@type": "ListItem", "position": i, "name": n, "url": absolute(base, p)}
                                               for i, (n, p) in enumerate(items[:100], 1)]}}


def _store_entity(settings: dict, base: str, ready: bool, image: str) -> dict:
    entity = store_schema(settings, base, ready, image)
    entity.pop("@context", None)
    return entity


def about_schema(base: str, settings: dict, ready: bool, image: str, name: str, description: str) -> dict:
    return {"@context": "https://schema.org", "@type": "AboutPage", "name": name, "description": description,
            "url": base + "/about", "inLanguage": "ar-SA", "mainEntity": _store_entity(settings, base, ready, image)}


def contact_schema(base: str, settings: dict, ready: bool, image: str, name: str, description: str) -> dict:
    return {"@context": "https://schema.org", "@type": "ContactPage", "name": name, "description": description,
            "url": base + "/contact", "inLanguage": "ar-SA", "mainEntity": _store_entity(settings, base, ready, image)}


def faq_schema(faq: list[tuple[str, str]]) -> dict:
    return {"@context": "https://schema.org", "@type": "FAQPage", "inLanguage": "ar-SA",
            "mainEntity": [{"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in faq]}


def store_schema(settings: dict, base: str, ready: bool, image: str) -> dict:
    schema: dict = {
        "@context": "https://schema.org",
        "@type": ["Store", "LocalBusiness"],
        "name": settings.get("store_name") or SITE_NAME_FALLBACK,
        "description": settings.get("seo_description", ""),
        "areaServed": {"@type": "Country", "name": "المملكة العربية السعودية"},
        "currenciesAccepted": "SAR",
        "inLanguage": "ar-SA",
    }
    if ready:
        schema["url"] = base + "/"
        if image:
            schema["image"] = absolute(base, image)
        if settings.get("phone"):
            schema["telephone"] = settings["phone"]
        if settings.get("map_url"):
            schema["hasMap"] = settings["map_url"]
        if settings.get("address") or settings.get("city"):
            schema["address"] = {"@type": "PostalAddress", "streetAddress": settings.get("address") or None,
                                 "addressLocality": settings.get("city") or "جدة", "addressCountry": "SA"}
            schema["address"] = {k: v for k, v in schema["address"].items() if v}
        if settings.get("instagram"):
            schema["sameAs"] = [settings["instagram"]]
    return schema


def website_schema(settings: dict, base: str) -> dict:
    return {"@context": "https://schema.org", "@type": "WebSite", "name": settings.get("store_name") or SITE_NAME_FALLBACK,
            "url": base + "/", "inLanguage": "ar-SA"}


def product_schema(product: dict, settings: dict, base: str) -> list[dict]:
    """Product + BreadcrumbList (Home > Category > Product, like the page's visible breadcrumbs).
    A price of 0 means 'price on request': no Offer is published (Google needs a real price)."""
    url = base + product_path(product["slug"])
    images = [absolute(base, i["url"]) for i in product.get("images", [])] or ([absolute(base, product.get("image", ""))] if product.get("image") else [])
    data: dict = {
        "@context": "https://schema.org", "@type": "Product", "name": product["name"], "url": url,
        "description": product.get("description", ""), "image": images, "sku": product.get("sku") or None,
        "category": product.get("category_name") or None,
        "brand": {"@type": "Brand", "name": settings.get("store_name") or SITE_NAME_FALLBACK},
    }
    if product.get("price", 0) > 0:
        data["offers"] = {
            "@type": "Offer", "url": url, "priceCurrency": "SAR", "price": str(product["price"]),
            "availability": "https://schema.org/InStock" if product.get("stock", 0) > 0 else "https://schema.org/OutOfStock",
            "seller": {"@type": "Organization", "name": settings.get("store_name") or SITE_NAME_FALLBACK},
        }
    data = {k: v for k, v in data.items() if v not in (None, "", [])}
    trail = [(HOME_LABEL, "/")]
    if product.get("category"):
        trail.append((product.get("category_name") or product["category"], category_path(product["category"])))
    trail.append((product["name"], product_path(product["slug"])))
    return [data, breadcrumb_schema(base, trail)]


_REPLACEMENTS = (
    ("title", re.compile(r"<title>.*?</title>", re.S), lambda v: f"<title>{esc(v)}</title>"),
    ("description", re.compile(r'<meta id="metaDescription"[^>]*>'), lambda v: f'<meta id="metaDescription" name="description" content="{esc(v)}">'),
    ("robots", re.compile(r'<meta id="robotsMeta"[^>]*>'), lambda v: f'<meta id="robotsMeta" name="robots" content="{esc(v)}">'),
    ("og_title", re.compile(r'<meta id="ogTitle"[^>]*>'), lambda v: f'<meta id="ogTitle" property="og:title" content="{esc(v)}">'),
    ("og_description", re.compile(r'<meta id="ogDescription"[^>]*>'), lambda v: f'<meta id="ogDescription" property="og:description" content="{esc(v)}">'),
    ("og_image", re.compile(r'<meta id="ogImage"[^>]*>'), lambda v: f'<meta id="ogImage" property="og:image" content="{esc(v)}">'),
    ("og_url", re.compile(r'<meta id="ogUrl"[^>]*>'), lambda v: f'<meta id="ogUrl" property="og:url" content="{esc(v)}">'),
    ("canonical", re.compile(r'<link id="canonicalLink"[^>]*>'), lambda v: f'<link id="canonicalLink" rel="canonical" href="{esc(v)}">' if v else '<link id="canonicalLink" rel="canonical">'),
)


def render_page(template: str, *, title: str, description: str, robots: str, canonical: str, og_url: str, og_image: str,
                og_type: str, schemas: list[dict], site_name: str, preload_image: str = "", image_alt: str = "") -> str:
    """Write the page-specific <head> into the shared index.html template."""
    values = {"title": title, "description": description, "robots": robots, "og_title": title, "og_description": description,
              "og_image": og_image, "og_url": og_url, "canonical": canonical}
    out = template
    for key, pattern, build in _REPLACEMENTS:
        out, count = pattern.subn(lambda _m, b=build, k=key: b(values[k]), out, count=1)
        if count != 1:
            raise RuntimeError(f"index.html is missing the '{key}' tag the server rewrites")
    out = out.replace('<meta property="og:type" content="website">', f'<meta property="og:type" content="{esc(og_type)}">', 1)
    # the page's own script rebuilds JSON-LD for the live store settings; the server writes the crawler-visible copy
    ld = "".join(f'<script{" id=\"localBusinessSchema\"" if i == 0 else ""} type="application/ld+json">{jsonld(sc)}</script>' for i, sc in enumerate(schemas))
    out, count = re.subn(r'<script id="localBusinessSchema" type="application/ld\+json">.*?</script>', lambda _m: ld, out, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError("index.html is missing the JSON-LD block the server rewrites")
    extra = [
        f'<meta property="og:site_name" content="{esc(site_name)}">',
        f'<meta property="og:image:alt" content="{esc(image_alt or title)}">' if og_image else "",
        '<meta name="twitter:card" content="summary_large_image">',
        f'<meta name="twitter:title" content="{esc(title)}">',
        f'<meta name="twitter:description" content="{esc(description)}">',
        f'<meta name="twitter:image" content="{esc(og_image)}">' if og_image else "",
        f'<link rel="alternate" hreflang="ar-SA" href="{esc(canonical)}">' if canonical else "",
        f'<link rel="alternate" hreflang="x-default" href="{esc(canonical)}">' if canonical else "",
        f'<link rel="preload" as="image" href="{esc(preload_image)}" fetchpriority="high">' if preload_image else "",
    ]
    return out.replace("</head>", "  " + "\n  ".join(e for e in extra if e) + "\n</head>", 1)


def sitemap_xml(base: str, products: list[dict], extra_pages: list[str], main_pages: list[tuple[str, str, str]] | None = None,
                categories: list[str] | None = None) -> str:
    """Home, the store pages (main_pages), legal pages (extra_pages), category pages (slugs) and every product with its photo."""
    urls = [f"<url><loc>{esc(base + '/')}</loc><changefreq>daily</changefreq><priority>1.0</priority></url>"]
    for page, freq, priority in main_pages or []:
        urls.append(f"<url><loc>{esc(base + page)}</loc><changefreq>{freq}</changefreq><priority>{priority}</priority></url>")
    for page in extra_pages:
        urls.append(f"<url><loc>{esc(base + page)}</loc><changefreq>yearly</changefreq><priority>0.3</priority></url>")
    for slug in categories or []:
        urls.append(f"<url><loc>{esc(base + category_path(slug))}</loc><changefreq>weekly</changefreq><priority>0.8</priority></url>")
    for p in products:
        loc = base + product_path(p["slug"])
        lastmod = (p.get("updated_at") or "")[:10]
        image = absolute(base, p.get("image", ""))
        urls.append(
            f"<url><loc>{esc(loc)}</loc>" + (f"<lastmod>{esc(lastmod)}</lastmod>" if lastmod else "")
            + "<changefreq>weekly</changefreq><priority>0.8</priority>"
            + (f"<image:image><image:loc>{esc(image)}</image:loc></image:image>" if image else "") + "</url>"
        )
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">'
            + "".join(urls) + "</urlset>")


def robots_txt(base: str, ready: bool) -> str:
    if not ready:
        return "User-agent: *\nDisallow: /\n"
    return f"User-agent: *\nAllow: /\nDisallow: /admin\nDisallow: /api/\nSitemap: {base}/sitemap.xml\n"
