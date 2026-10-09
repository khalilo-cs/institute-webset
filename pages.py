"""Server-rendered sub-pages of the storefront: pure functions, no I/O.

app.py swaps the markup returned here into the shared index.html shell (between <!--main:start--> and <!--main:end-->),
so the header, footer, cart drawer, dialogs and scripts are identical on every page and the storefront script hydrates
the data-* hooks (add to cart, favourites, quick view, gallery, copy link, contact form).

Rules kept here:
- every value that comes from the database, the settings or the visitor goes through esc();
- no inline event handlers, no javascript: URLs, no external hosts (strict CSP with per-response nonces);
- no invented facts: only the store settings, the catalogue and the services the shop already lists.
"""
from __future__ import annotations

import re

from seo import category_path, esc, product_path

HOME_CRUMB = ("الرئيسية", "/")
ALL_PRODUCTS = "كل المنتجات"

# The single source of the FAQ: rendered on the home page (between <!--faq:start--> and <!--faq:end-->), on /faq and
# in the FAQPage structured data.
FAQ = [
    ("كيف أقيس النافذة قبل طلب الستارة؟",
     "قِس عرض النافذة وارتفاعها، ووضّح للمتجر نوع التركيب ومكان المسار الذي تريده. قد تختلف طريقة القياس بحسب نوع الستارة، لذلك أكّد الأبعاد النهائية قبل بدء التفصيل."),
    ("ما أنواع الستائر التي يوفرها المتجر؟",
     "بحسب معلومات المتجر، تشمل الخيارات تفصيل أنواع مختلفة من الستائر، وستائر رول، وشرائح معدنية، وستائر كهربائية. أكّد توفر القماش والموديل والقياسات مباشرة مع المتجر."),
    ("هل الأسعار نهائية للستائر المفصلة؟",
     "يُحدَّد السعر النهائي للستائر المفصّلة بحسب القماش والمقاس والطبقات والإكسسوارات. يراجع فريق المتجر تفاصيل القياس ويؤكد السعر النهائي قبل اعتماد العمل."),
    ("كيف أطلب تفصيل ستارة؟",
     "اختر المنتج أو نوع القماش وأرسل طلبك من الموقع مع بيانات التواصل والمدينة. يتواصل المتجر لتأكيد نوع القماش والمقاس والسعر وموعد التجهيز قبل اعتماد التفصيل."),
    ("هل توجد ستائر كهربائية؟",
     "يظهر في أقسام الموقع خيار الستائر الكهربائية. يرجى تأكيد نوع المحرك وطريقة التحكم والتركيب والتوافق مع النافذة قبل الطلب."),
]

_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="{w}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">{d}</svg>'


def _icon(paths: str, width: str = "1.7") -> str:
    return _SVG.format(w=width, d=paths)


# Same drawings as the storefront script's icons.heart / icons.arrow, so server and client cards look identical.
HEART = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
         '<path d="M20.8 8.8c0 5.1-8.8 10.1-8.8 10.1S3.2 13.9 3.2 8.8a4.5 4.5 0 0 1 8.1-2.7.9.9 0 0 0 1.4 0 4.5 4.5 0 0 1 8.1 2.7Z"/></svg>')
ARROW = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
         '<path d="M19 12H5m7-7-7 7 7 7"/></svg>')
ICONS = {
    "phone": _icon('<path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2"/>'),
    "chat": _icon('<path d="M20 11.5a8.1 8.1 0 0 1-11.9 7.1L4 20l1.4-3.8A8.1 8.1 0 1 1 20 11.5Z"/><path d="M9 9c.4 1.7 1.4 2.7 3.2 3.5"/>'),
    "pin": _icon('<path d="M12 21s-7-6.1-7-11.5a7 7 0 0 1 14 0C19 14.9 12 21 12 21Z"/><circle cx="12" cy="9.5" r="2.5"/>'),
    "clock": _icon('<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>'),
    "city": _icon('<path d="M4 21V8l6-4v17M10 21h10V11l-5-3"/><path d="M14 13h2M14 17h2M6.5 11h1M6.5 15h1"/>'),
    "camera": _icon('<rect x="3.5" y="3.5" width="17" height="17" rx="5"/><circle cx="12" cy="12" r="4"/><circle cx="17.2" cy="6.8" r=".8"/>'),
    "search": _icon('<circle cx="10.8" cy="10.8" r="6.8"/><path d="m16 16 4.2 4.2"/>'),
    "home": _icon('<path d="M4 11.5 12 5l8 6.5V20H4z"/><path d="M10 20v-5h4v5"/>'),
    "grid": _icon('<rect x="4" y="4" width="7" height="7" rx="2"/><rect x="13" y="4" width="7" height="7" rx="2"/><rect x="4" y="13" width="7" height="7" rx="2"/><rect x="13" y="13" width="7" height="7" rx="2"/>'),
    "bag": _icon('<path d="M3 4h2l2.2 11.3a2 2 0 0 0 2 1.6h8.4a2 2 0 0 0 1.9-1.4L21 8H6"/><circle cx="10" cy="20" r="1"/><circle cx="18" cy="20" r="1"/>'),
    "help": _icon('<circle cx="12" cy="12" r="8.5"/><path d="M9.6 9.5a2.5 2.5 0 1 1 3.4 2.3c-.6.3-1 .8-1 1.5v.4"/><path d="M12 16.8h.01"/>'),
}

# The services the shop already lists on the site (no new claims). slug = the matching default category.
SERVICES = [
    ("curtains", "تفصيل ستائر حسب المقاس",
     "تُفصَّل الستائر وفق مقاس النافذة والقماش الذي تختاره، ويؤكد المتجر القياسات والتكلفة النهائية معك قبل البدء.",
     _icon('<circle cx="6" cy="7" r="2.5"/><circle cx="6" cy="17" r="2.5"/><path d="M8 8.5 20 17M8 15.5 20 7"/>', "1.6")),
    ("roller", "ستائر رول",
     "ستائر رول تناسب بعض النوافذ والمساحات العملية؛ اسأل عن الخامات والمقاسات المتوفرة.",
     _icon('<rect x="4" y="3.5" width="16" height="3" rx="1.5"/><path d="M6 6.5V17h12V6.5M12 17v3"/>', "1.6")),
    ("blinds", "ستائر شرائح معدنية",
     "ستائر شرائح معدنية لبعض النوافذ والمساحات العملية؛ تواصل مع المتجر لمعرفة الخيارات والمقاسات المتاحة.",
     _icon('<path d="M4 4h16M5 8h14M5 12h14M5 16h14M12 4v16"/>', "1.6")),
    ("electric", "ستائر كهربائية",
     "ستائر تُفتح وتُغلق كهربائيًا؛ يُؤكَّد نوع المحرك وطريقة التحكم والتركيب والتوافق مع النافذة قبل الطلب.",
     _icon('<path d="M13 3 6 13h5l-1 8 7-10h-5l1-8Z"/>', "1.6")),
    ("fabrics", "أقمشة الستائر",
     "أقمشة ستائر بدرجات وخامات متنوعة؛ اتفق مع المتجر على النوع والكمية والمقاس المطلوب.",
     _icon('<path d="M4 6c2.7-2 5.3 2 8 0s5.3-2 8 0M4 12c2.7-2 5.3 2 8 0s5.3-2 8 0M4 18c2.7-2 5.3 2 8 0s5.3-2 8 0"/>', "1.6")),
]

SORT_OPTIONS = ('<option value="featured">ترتيب مقترح</option><option value="low">السعر: الأقل أولًا</option>'
                '<option value="high">السعر: الأعلى أولًا</option><option value="name">الاسم: أ إلى ي</option>')
NEW_TAB = '<span class="sr-only"> (يفتح في نافذة جديدة)</span>'
_DIGITS = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")


# -- small helpers ---------------------------------------------------------------------------------------------------
def store_name(settings: dict) -> str:
    return settings.get("store_name") or "الفخامة للأقمشة والستائر"


def arabic_number(value: int) -> str:
    """1450 -> '١٬٤٥٠' (what Intl.NumberFormat('ar-SA') prints in the storefront script)."""
    return f"{int(value):,}".replace(",", "٬").translate(_DIGITS)

def count_ar(n: int, unit: str = "product") -> str:
    """Arabic number agreement: منتج واحد / منتجان / ٣ منتجات / ١١ منتجًا (and قطعة / قطعتان / قطع / قطعة)."""
    n = int(n or 0)
    one, two, few, many, zero = {"product": ("منتج واحد", "منتجان", "منتجات", "منتجًا", "لا توجد منتجات"),
                                 "piece": ("قطعة واحدة", "قطعتان", "قطع", "قطعة", "لا توجد قطع")}[unit]
    if n == 0: return zero
    if n == 1: return one
    if n == 2: return two
    return f"{arabic_number(n)} {few if 3 <= n % 100 <= 10 else many}"


def fill_store(document: str, settings: dict) -> str:
    """The footer's city and phone come from the settings in the HTML itself (no-JS visitors and crawlers never
    see the template's placeholders)."""
    city = settings.get("city") or "جدة"
    out = document.replace("<span data-store-city>أضف المدينة</span>", f"<span data-store-city>{esc(city)}</span>")
    phone = settings.get("phone") or ""
    tel = re.sub(r"[^\d+]", "", phone)
    contact = (f'<a href="tel:{esc(tel)}" dir="ltr">{esc(phone)}</a>' if len(re.sub(r"\D", "", tel)) >= 6
               else esc(settings.get("whatsapp") or "تواصل معنا من صفحة التواصل"))
    return out.replace('id="storeContact">أضف رقم الهاتف أو واتساب</span>', f'id="storeContact">{contact}</span>')



def on_request(product: dict) -> bool:
    try:
        return int(product.get("price") or 0) <= 0
    except (TypeError, ValueError):
        return True


def price_html(product: dict) -> str:
    """Identical to the storefront script's priceHtml(): price 0 = made to order, confirmed after measuring."""
    if on_request(product):
        return '<span class="on-request">السعر حسب الطلب</span>'
    return f'{arabic_number(product["price"])} <small>ر.س</small>'


def in_stock(product: dict) -> bool:
    try:
        return int(product.get("stock") or 0) > 0
    except (TypeError, ValueError):
        return False


def web_url(url: str) -> str:
    """Only http(s) links may end up in an href (no javascript:, data: ...)."""
    url = (url or "").strip()
    return url if re.fullmatch(r"https?://[^\s\"'<>`]+", url, re.I) else ""


def tel_href(phone: str) -> str:
    digits = "".join(c for c in (phone or "") if c.isdigit() or c == "+")
    return f"tel:{digits}" if len(re.sub(r"\D", "", digits)) >= 6 else ""


def whatsapp_href(number: str) -> str:
    digits = re.sub(r"\D", "", number or "")
    return f"https://wa.me/{digits}" if len(digits) >= 8 else ""


def text_lines(text: str) -> str:
    """Escaped text with its single line breaks kept."""
    return "<br>".join(esc(line.strip()) for line in (text or "").strip().splitlines() if line.strip())


def paragraphs(text: str, cls: str = "") -> str:
    """Escaped <p> per blank-line separated block (single newlines become <br>)."""
    attr = f' class="{cls}"' if cls else ""
    blocks = [b for b in re.split(r"\n\s*\n", (text or "").replace("\r\n", "\n").strip()) if b.strip()]
    return "".join(f"<p{attr}>{text_lines(b)}</p>" for b in blocks)


def search_matches(product: dict, term: str) -> bool:
    """Same haystack as the storefront script's search (name, category, description, SKU)."""
    hay = f'{product.get("name", "")} {product.get("category_name", "")} {product.get("description", "")} {product.get("sku", "")}'
    return term.lower() in hay.lower()


# -- building blocks --------------------------------------------------------------------------------------------------
def breadcrumbs(items: list[tuple[str, str | None]]) -> str:
    """The last item is the current page: aria-current, not a link."""
    out = []
    for i, (label, href) in enumerate(items):
        if i == len(items) - 1 or not href:
            out.append(f'<li><span aria-current="page">{esc(label)}</span></li>')
        else:
            out.append(f'<li><a href="{esc(href)}">{esc(label)}</a></li>')
    return f'<nav class="breadcrumbs" aria-label="مسار التنقل"><ol>{"".join(out)}</ol></nav>'


def page_hero(crumbs, *, eyebrow: str, title: str, intro_html: str = "", extra: str = "", media: str = "") -> str:
    """Dark hero that opens every sub-page: breadcrumbs, eyebrow, the page's only <h1>, intro.
    intro_html and extra are HTML: callers escape what they put in them. media = decorative backdrop image URL."""
    image = ""
    cls = "page-hero on-dark"
    if media:
        cls += " has-media"
        image = f'<img class="page-hero-media" src="{esc(media)}" alt="" decoding="async" fetchpriority="high">'
    intro = f'<p class="page-hero-intro">{intro_html}</p>' if intro_html else ""
    return (f'<section class="{cls}" aria-labelledby="pageTitle">{image}<div class="container">{breadcrumbs(crumbs)}'
            f'<span class="eyebrow">{esc(eyebrow)}</span><h1 id="pageTitle">{esc(title)}</h1>{intro}{extra}</div></section>')


def product_card(p: dict) -> str:
    """Exactly the card the storefront script's renderProducts() builds (not yet a favourite), except that the name
    links to the product's own page."""
    out = not in_stock(p)
    name = esc(p.get("name", ""))
    pid = esc(p.get("id", ""))
    badge = "نفد المخزون" if out else esc(p.get("badge") or "متوفر")
    quick = "عرض التفاصيل" if out else "عرض التفاصيل وإضافة للسلة"
    return (f'<article class="product-card"><div class="product-image-wrap">'
            f'<img class="product-image" src="{esc(p.get("thumb") or p.get("image") or "")}" alt="{esc(p.get("alt") or p.get("name", ""))}" loading="lazy" decoding="async">'
            f'<span class="product-badge">{badge}</span>'
            f'<button class="favorite-btn" data-favorite="{pid}" aria-label="إضافة إلى المفضلة: {name}" aria-pressed="false">{HEART}</button>'
            f'<button class="quick-add" data-quick="{pid}">{quick}</button></div>'
            f'<div class="product-info"><p class="product-category">{esc(p.get("category_name") or "ستائر وأقمشة")}</p>'
            f'<h3 class="product-name"><a class="product-link" href="{esc(product_path(p.get("slug", "")))}">{name}</a></h3>'
            f'<div class="product-bottom"><span class="product-price">{price_html(p)}</span>'
            f'<span class="rating"><span aria-hidden="true">✦</span> {"غير متوفر" if out else "متاح"}</span></div></div></article>')


def category_card(c: dict) -> str:
    """Same look as the home page's category cards; the photo is decorative (the heading names the link)."""
    return (f'<a class="category-card" href="{esc(category_path(c["slug"]))}">'
            f'<img src="{esc(c.get("cover") or "")}" alt="" loading="lazy" decoding="async">'
            f'<div class="category-card-content"><div><h3>{esc(c["name"])}</h3><p>{count_ar(c.get("count") or 0)}</p></div>'
            f'<span class="round-arrow">{ARROW}</span></div></a>')


def no_results(title: str, text: str) -> str:
    return f'<div class="no-results"><b>{esc(title)}</b><span>{esc(text)}</span></div>'


def shop_section(products: list[dict], *, heading: str, filters: list[dict] | None, empty: tuple[str, str]) -> str:
    """The catalogue block with the same ids as the home page (#filterList, #resultCount, #sortSelect, #productGrid).
    filters=None leaves #filterList out (a category page filters to body[data-category])."""
    chips = ""
    if filters is not None:
        chips = ('<div class="filter-list" id="filterList" role="group" aria-label="تصفية حسب القسم">'
                 '<button class="filter-chip active" data-filter="all">الكل</button>'
                 + "".join(f'<button class="filter-chip" data-filter="{esc(c["slug"])}">{esc(c["name"])}</button>' for c in filters)
                 + "</div>")
    cards = "".join(product_card(p) for p in products) or no_results(*empty)
    return (f'<section class="section shop-section" id="shop" aria-labelledby="shopTitle"><div class="container">'
            f'<h2 class="sr-only" id="shopTitle">{esc(heading)}</h2>'
            f'<div class="toolbar">{chips}<span class="result-count" id="resultCount" aria-live="polite">{count_ar(len(products))}</span>'
            f'<label><span class="sr-only">ترتيب المنتجات</span><select class="sort-select" id="sortSelect">{SORT_OPTIONS}</select></label></div>'
            f'<div class="product-grid" id="productGrid" aria-live="polite">{cards}</div>'
            '<p class="product-footnote" id="productFootnote">«السعر حسب الطلب» يعني أن السعر يُحدَّد بعد تأكيد المقاس والخامة. '
            'لا يوجد دفع إلكتروني؛ يُسجَّل الطلب للمراجعة ويتواصل المتجر لتأكيده.</p>'
            '</div></section>')


def cta_band(eyebrow: str, title: str, text: str, actions: list[tuple[str, str, bool]], title_id: str = "ctaTitle") -> str:
    """Dark call-to-action card (reuses the home page's .contact-card look). actions = (href, label, primary)."""
    links = "".join(f'<a class="btn {"btn-primary" if primary else "btn-outline"}" href="{esc(href)}">{esc(label)}</a>'
                    for href, label, primary in actions)
    return (f'<section class="contact-section page-cta" aria-labelledby="{title_id}"><div class="container"><div class="contact-card on-dark">'
            f'<div class="contact-copy"><span class="eyebrow">{esc(eyebrow)}</span><h2 id="{title_id}">{esc(title)}</h2><p>{esc(text)}</p></div>'
            f'<div class="contact-actions">{links}</div></div></div></section>')


def render_faq_items(faq: list[tuple[str, str]] | None = None, indent: str = "          ") -> str:
    """<details> items for the home page's FAQ block and the /faq page."""
    return "\n".join(f"{indent}<details><summary>{esc(q)}</summary><p>{esc(a)}</p></details>" for q, a in (faq or FAQ))


def info_list(settings: dict, *, include_whatsapp: bool = True) -> str:
    """Address/map, phone, WhatsApp (only when the owner set a number), opening hours (only when set), city."""
    items = []

    def item(icon: str, label: str, value: str) -> None:
        items.append(f'<li><span class="info-icon" aria-hidden="true">{ICONS[icon]}</span>'
                     f'<div><span class="info-label">{esc(label)}</span><div class="info-value">{value}</div></div></li>')

    address, map_url = settings.get("address", ""), web_url(settings.get("map_url", ""))
    if address or map_url:
        value = esc(address)
        if map_url:
            value += (f'{"<br>" if address else ""}<a href="{esc(map_url)}" target="_blank" rel="noopener">'
                      f'عرض الموقع على الخريطة{NEW_TAB}</a>')
        item("pin", "العنوان", value)
    phone, tel = settings.get("phone", ""), tel_href(settings.get("phone", ""))
    if phone and tel:
        item("phone", "الهاتف", f'<a href="{esc(tel)}" dir="ltr">{esc(phone)}</a>')
    wa = whatsapp_href(settings.get("whatsapp", "")) if include_whatsapp else ""
    if wa:
        item("chat", "واتساب", f'<a href="{esc(wa)}" target="_blank" rel="noopener" dir="ltr">{esc(settings["whatsapp"])}{NEW_TAB}</a>')
    if (settings.get("opening_hours") or "").strip():
        item("clock", "أوقات العمل", text_lines(settings["opening_hours"]))
    if settings.get("city"):
        item("city", "المدينة", f'{esc(settings["city"])}، المملكة العربية السعودية')
    insta = web_url(settings.get("instagram", ""))
    if insta:
        item("camera", "إنستغرام", f'<a href="{esc(insta)}" target="_blank" rel="noopener">حساب المتجر{NEW_TAB}</a>')
    return f'<ul class="info-list">{"".join(items)}</ul>'


# -- pages ------------------------------------------------------------------------------------------------------------
def render_products(settings: dict, products: list[dict], categories: list[dict], q: str = "") -> str:
    """/products — every active product; ?q= filters the server-rendered grid (the script keeps it in sync)."""
    term = (q or "").strip()[:100]
    shown = [p for p in products if search_matches(p, term)] if term else products
    store = store_name(settings)
    if term:
        intro = f"نتائج البحث عن «{esc(term)}»: {count_ar(len(shown))}. امسح البحث لعرض كل المنتجات."
    else:
        intro = (f"ستائر وأقمشة {esc(store)} في {esc(settings.get('city') or 'جدة')}. ابحث ورتّب واختر القسم، "
                 "وافتح أي منتج لمشاهدة صوره وتفاصيله.")
    hero = page_hero([HOME_CRUMB, (ALL_PRODUCTS, None)], eyebrow="تشكيلة المتجر", title=ALL_PRODUCTS, intro_html=intro)
    empty = ("لم نعثر على نتيجة مطابقة", "جرّب كلمة أخرى أو تصفّح الأقسام.") if term else (
        "لا توجد منتجات معروضة حاليًا", "تواصل مع المتجر للاستفسار عن الخيارات المتاحة.")
    return (hero + shop_section(shown, heading="قائمة المنتجات", filters=categories, empty=empty)
            + cta_band("تحتاج مساعدة؟", "لست متأكدًا من الاختيار؟",
                       "اسأل المتجر عن القماش والمقاس وطريقة التركيب قبل إرسال طلبك.",
                       [("/contact", "تواصل معنا", True), ("/faq", "الأسئلة الشائعة", False)]))


def render_categories(settings: dict, categories: list[dict]) -> str:
    """/categories — active categories that hold at least one active product."""
    hero = page_hero([HOME_CRUMB, ("الأقسام", None)], eyebrow="تصفّح حسب النوع", title="الأقسام",
                     intro_html="اختر القسم المناسب لنافذتك. تظهر هنا الأقسام التي تضم منتجات معروضة حاليًا.")
    grid = "".join(category_card(c) for c in categories) or no_results("لا توجد أقسام معروضة حاليًا", "تواصل مع المتجر للاستفسار.")
    return (hero + '<section class="section categories-section" aria-labelledby="categoriesTitle"><div class="container">'
            '<h2 class="sr-only" id="categoriesTitle">قائمة الأقسام</h2>'
            f'<div class="category-grid" id="categoryIndex">{grid}</div>'
            f'<p class="page-more"><a class="text-link" href="/products">{ALL_PRODUCTS} {ARROW}</a></p></div></section>'
            + cta_band("نساعدك في الاختيار", "لم تجد ما تبحث عنه؟",
                       "اسأل المتجر عن الأقمشة والأنواع المتاحة والمقاسات قبل الطلب.",
                       [("/contact", "تواصل معنا", True), ("/products", ALL_PRODUCTS, False)]))


def category_switch(categories: list[dict], current: str) -> str:
    links = [f'<li><a href="/products">{ALL_PRODUCTS}</a></li>']
    for c in categories:
        cur = ' aria-current="page"' if c["slug"] == current else ""
        links.append(f'<li><a href="{esc(category_path(c["slug"]))}"{cur}>{esc(c["name"])} <span class="count">({int(c.get("count") or 0)})</span></a></li>')
    return (f'<div class="category-switch-band"><div class="container"><nav class="category-switch" aria-label="الأقسام">'
            f'<ul>{"".join(links)}</ul></nav></div></div>')


def render_category(settings: dict, category: dict, categories: list[dict], products: list[dict]) -> str:
    """/category/<slug> — hero with the category's name, description and image, switcher, then its products."""
    description = (category.get("description") or "").strip()
    intro = text_lines(description) if description else (
        f"{count_ar(len(products))} في قسم {esc(category['name'])} من {esc(store_name(settings))}.")
    backdrop = category.get("image") or (products[0].get("image") if products else "") or ""
    hero = page_hero([HOME_CRUMB, ("الأقسام", "/categories"), (category["name"], None)], eyebrow="قسم", title=category["name"],
                     intro_html=intro, media=backdrop)
    switch = category_switch(categories, category["slug"]) if categories else ""
    return (hero + switch
            + shop_section(products, heading=f"منتجات {category['name']}", filters=None,
                           empty=("لا توجد منتجات في هذا القسم حاليًا", "تصفّح الأقسام الأخرى أو تواصل مع المتجر."))
            + cta_band("تحتاج مساعدة؟", "لست متأكدًا من الاختيار؟",
                       "اسأل المتجر عن القماش والمقاس وطريقة التركيب قبل إرسال طلبك.",
                       [("/contact", "تواصل معنا", True), ("/categories", "كل الأقسام", False)]))


def render_product(settings: dict, product: dict, related: list[dict], base: str) -> str:
    """/product/<slug> — a real page: gallery, details, add to cart, share, notes, and related products."""
    from urllib.parse import quote  # local: only this page builds share links
    pid = esc(product["id"])
    url = base + product_path(product["slug"])
    images = product.get("images") or ([{"url": product["image"], "thumb": product["image"]}] if product.get("image") else [])
    alt = product.get("alt") or product["name"]
    if images:
        main = (f'<div class="product-gallery-main"><img id="pageGalleryMain" src="{esc(images[0]["url"])}" alt="{esc(alt)}" '
                f'decoding="async" fetchpriority="high" data-count="{len(images)}"></div>')
    else:
        main = '<div class="product-gallery-main is-empty" aria-hidden="true"></div>'
    thumbs = ""
    if len(images) > 1:
        buttons = "".join(
            f'<button type="button" class="gthumb{"" if i else " active"}" data-page-gindex="{i}" data-full="{esc(im["url"])}" '
            f'aria-label="الصورة {i + 1} من {len(images)}" aria-pressed="{"false" if i else "true"}">'
            f'<img src="{esc(im.get("thumb") or im["url"])}" alt="" loading="lazy" decoding="async"></button>'
            for i, im in enumerate(images))
        thumbs = f'<div class="product-gallery-thumbs" role="group" aria-label="صور المنتج">{buttons}</div>'
    available = in_stock(product)
    if on_request(product):
        price = '<span class="on-request">السعر حسب الطلب</span>'
    else:
        price = f'{arabic_number(product["price"])} <small>ريال سعودي</small>'
    if not available:
        stock = '<p class="product-page-stock is-out">غير متوفر حاليًا</p>'
    elif on_request(product):
        stock = '<p class="product-page-stock">متاح للتفصيل حسب الطلب</p>'
    else:
        stock = f'<p class="product-page-stock">متوفر — المتاح حاليًا: {count_ar(product["stock"], "piece")}</p>'
    if available:
        add = f'<button class="btn btn-primary" type="button" data-add-product="{pid}">أضف إلى السلة {ARROW}</button>'
    else:
        add = f'<button class="btn btn-primary" type="button" data-add-product="{pid}" disabled>غير متوفر حاليًا</button>'
    share = "https://wa.me/?text=" + quote(f"{product['name']} — {store_name(settings)}\n{url}", safe="")
    notes = []
    if on_request(product):
        notes.append("يُفصَّل حسب الطلب؛ يؤكد المتجر المقاس والخامة والسعر النهائي معك قبل البدء.")
    if (settings.get("delivery_note") or "").strip():
        notes.append(settings["delivery_note"].strip())
    try:
        fee = int(settings.get("delivery_fee") or 0)
    except (TypeError, ValueError):
        fee = 0
    if fee > 0:
        notes.append(f"رسوم التوصيل: {arabic_number(fee)} ر.س")
    notes.append("لا يوجد دفع إلكتروني؛ يُسجَّل الطلب للمراجعة ويتواصل معك المتجر لتأكيده.")
    sku = f'<p class="product-page-sku">رمز المنتج: <span dir="ltr">{esc(product["sku"])}</span></p>' if product.get("sku") else ""
    category_link = (f'<a class="eyebrow product-page-category" href="{esc(category_path(product["category"]))}">'
                     f'{esc(product.get("category_name") or "ستائر وأقمشة")}</a>')
    description = paragraphs(product.get("description", "")) or "<p>تواصل مع المتجر لمعرفة تفاصيل هذا المنتج.</p>"
    crumbs = [HOME_CRUMB, (product.get("category_name") or "الأقسام", category_path(product["category"])), (product["name"], None)]
    article = (f'<section class="page-hero page-hero--compact"><div class="container">{breadcrumbs(crumbs)}</div></section>'
               f'<article class="product-page" aria-labelledby="productTitle" data-product-id="{pid}"><div class="container product-page-grid">'
               f'<div class="product-gallery">{main}{thumbs}</div>'
               f'<div class="product-summary">{category_link}<h1 id="productTitle">{esc(product["name"])}</h1>'
               f'<p class="product-page-price">{price}</p>{stock}<div class="product-page-desc">{description}</div>'
               f'<div class="product-page-actions">{add}'
               f'<a class="btn btn-outline" href="{esc(share)}" target="_blank" rel="noopener">مشاركة عبر واتساب{NEW_TAB}</a>'
               f'<button class="btn btn-outline" type="button" data-copy-link="{esc(url)}">نسخ الرابط</button></div>'
               f'<ul class="product-page-notes">{"".join(f"<li>{esc(n)}</li>" for n in notes)}</ul>{sku}</div></div></article>')
    if related:
        article += (f'<section class="section related-section" aria-labelledby="relatedTitle"><div class="container">'
                    f'<div class="section-head"><div><span class="eyebrow">من القسم نفسه</span><h2 id="relatedTitle">قد يعجبك أيضًا</h2></div>'
                    f'<a class="text-link" href="{esc(category_path(product["category"]))}">كل منتجات القسم {ARROW}</a></div>'
                    f'<div class="product-grid" id="relatedGrid">{"".join(product_card(p) for p in related[:4])}</div></div></section>')
    return article


def render_about(settings: dict, categories: list[dict], image: tuple[str, str] | None = None) -> str:
    """/about — the owner's text from the settings, the services the shop lists, how to visit, and next steps."""
    title = (settings.get("about_title") or "").strip() or "من نحن"
    store = store_name(settings)
    tagline = (settings.get("tagline") or "").strip()
    hero = page_hero([HOME_CRUMB, (title, None)], eyebrow="تعرّف علينا", title=title,
                     intro_html=esc(f"{store} — {tagline}" if tagline else store))
    body = paragraphs(settings.get("about_body", "")) or f"<p>{esc(store)}</p>"
    figure = ""
    if image and image[0]:
        figure = f'<figure class="about-figure"><img src="{esc(image[0])}" alt="{esc(image[1])}" loading="lazy" decoding="async"></figure>'
    story = (f'<section class="section about-story" aria-labelledby="aboutStoryTitle"><div class="container about-grid'
             f'{"" if figure else " no-figure"}"><div class="prose"><h2 class="sr-only" id="aboutStoryTitle">قصتنا</h2>{body}</div>{figure}</div></section>')
    visible = {c["slug"]: c["name"] for c in categories}
    cards = []
    for slug, name, text, icon in SERVICES:
        link = (f'<a class="text-link" href="{esc(category_path(slug))}">تصفّح {esc(visible[slug])} {ARROW}</a>'
                if slug in visible else "")
        cards.append(f'<article class="service-card"><span class="service-icon" aria-hidden="true">{icon}</span>'
                     f'<h3>{esc(name)}</h3><p>{esc(text)}</p>{link}</article>')
    offer = ('<section class="section services about-services" aria-labelledby="offerTitle"><div class="container">'
             '<div class="section-head"><div><span class="eyebrow">خدماتنا</span><h2 id="offerTitle">ما نقدمه</h2>'
             '<p>من اختيار القماش إلى التفصيل؛ تواصل مع المتجر لتأكيد الخامات والمقاسات المتاحة.</p></div></div>'
             f'<div class="service-grid">{"".join(cards)}</div></div></section>')
    city = esc(settings.get("city") or "جدة")
    visit = ('<section class="section visit-section" aria-labelledby="visitTitle"><div class="container"><div class="visit-card on-dark">'
             f'<div class="visit-copy"><span class="eyebrow">زورونا</span><h2 id="visitTitle">زورونا في المتجر</h2>'
             f'<p>نخدم عملاءنا في {city}. يمكنك زيارة المتجر لمعاينة الخيارات، أو الاتصال بنا للاستفسار قبل الزيارة.</p></div>'
             f'{info_list(settings, include_whatsapp=True)}</div></div></section>')
    cta = cta_band("الخطوة التالية", "ابدأ باختيار ستارتك", "تصفّح المنتجات بصورها، أو أرسل استفسارك وسيراجعه المتجر.",
                   [("/products", "تصفّح المنتجات", True), ("/contact", "تواصل معنا", False)])
    return hero + story + offer + visit + cta


def contact_form(values: dict | None = None, notice: tuple[str, str] | None = None) -> str:
    v = {k: str(x) for k, x in (values or {}).items() if isinstance(x, (str, int))}
    state, message = notice or ("", "")
    status_attr = f' data-state="{esc(state)}"' if state else ""
    checked = " checked" if (values or {}).get("consent") is True else ""
    return ('<form id="contactForm" class="contact-form" method="post" action="/api/contact">'
            '<div class="form-grid">'
            f'<div class="form-field"><label for="contactName">الاسم</label><input id="contactName" name="name" required minlength="2" maxlength="80" autocomplete="name" value="{esc(v.get("name", ""))}"></div>'
            f'<div class="form-field"><label for="contactPhone">رقم الجوال</label><input id="contactPhone" name="phone" type="tel" inputmode="tel" required minlength="8" maxlength="24" autocomplete="tel" dir="ltr" placeholder="05xxxxxxxx" value="{esc(v.get("phone", ""))}"></div>'
            f'<div class="form-field full"><label for="contactEmail">البريد الإلكتروني (اختياري)</label><input id="contactEmail" name="email" type="email" maxlength="120" autocomplete="email" dir="ltr" value="{esc(v.get("email", ""))}"></div>'
            f'<div class="form-field full"><label for="contactMessage">رسالتك</label><textarea id="contactMessage" name="message" required minlength="5" maxlength="2000" rows="5">{esc(v.get("message", ""))}</textarea></div>'
            '</div>'
            '<div class="hp" aria-hidden="true"><label for="contactWebsite">اترك هذا الحقل فارغًا</label>'
            '<input id="contactWebsite" name="website" type="text" tabindex="-1" autocomplete="off"></div>'
            '<input type="hidden" name="page" value="/contact">'
            f'<label class="consent"><input type="checkbox" name="consent" value="1" required{checked}> '
            '<span>أوافق على <a href="/privacy" target="_blank" rel="noopener">سياسة الخصوصية<span class="sr-only"> (تفتح في نافذة جديدة)</span></a> '
            'وعلى استخدام بياناتي للرد على رسالتي.</span></label>'
            '<button class="btn btn-primary" type="submit">إرسال الرسالة</button>'
            f'<p id="contactStatus" class="form-status" role="status" aria-live="polite" tabindex="-1"{status_attr}>{esc(message)}</p>'
            '</form>')


def render_contact(settings: dict, notice: tuple[str, str] | None = None, values: dict | None = None) -> str:
    """/contact — contact details from the settings and the enquiry form (POST /api/contact).
    notice = ("success"|"error", text) is used when the form was sent without JavaScript."""
    hero = page_hero([HOME_CRUMB, ("تواصل معنا", None)], eyebrow="نسعد بخدمتك", title="تواصل معنا",
                     intro_html="اسأل عن الأقمشة والمقاسات وتفصيل الستائر، أو اترك رسالتك ليراجعها المتجر ويتواصل معك.")
    tel = tel_href(settings.get("phone", ""))
    phone_link = f'<a href="{esc(tel)}" dir="ltr">{esc(settings.get("phone", ""))}</a>' if tel else "بيانات التواصل في المتجر"
    info = ('<div class="visit-card contact-info-card on-dark"><div class="visit-copy"><span class="eyebrow">بيانات المتجر</span>'
            '<h2 id="contactInfoTitle">بيانات التواصل</h2><p>للاستفسار السريع اتصل بالمتجر مباشرة، أو زرنا لمعاينة الخيارات.</p></div>'
            f'{info_list(settings)}</div>')
    form = ('<div class="contact-form-card"><h2 id="contactFormTitle">أرسل رسالة</h2>'
            '<p>الاسم ورقم الجوال والرسالة مطلوبة، والبريد الإلكتروني اختياري. نستخدم بياناتك للرد على رسالتك فقط.</p>'
            f'{contact_form(values, notice)}'
            f'<noscript><p class="noscript-note">يمكنك أيضًا الاتصال بالمتجر مباشرة: {phone_link}</p></noscript></div>')
    return (hero + '<section class="section contact-page" aria-label="التواصل مع المتجر"><div class="container contact-layout">'
            f'{info}{form}</div></section>')


def render_faq(settings: dict) -> str:
    """/faq — the shared FAQ list, then a way to ask anything else."""
    hero = page_hero([HOME_CRUMB, ("الأسئلة الشائعة", None)], eyebrow="قبل تفصيل الستارة", title="الأسئلة الشائعة",
                     intro_html="إجابات عن القياس والخامات والأسعار وطريقة الطلب. إن لم تجد ما تبحث عنه، تواصل مع المتجر.")
    items = render_faq_items(indent="")
    return (hero + '<section class="section faq-page" aria-labelledby="faqListTitle"><div class="container">'
            f'<h2 class="sr-only" id="faqListTitle">الأسئلة والأجوبة</h2><div class="faq-list">{items}</div></div></section>'
            + cta_band("ما زال لديك سؤال؟", "لم تجد إجابتك؟", "أرسل سؤالك إلى المتجر أو تصفّح المنتجات بصورها وتفاصيلها.",
                       [("/contact", "تواصل معنا", True), ("/products", "تصفّح المنتجات", False)]))


NOT_FOUND_INTRO = {
    "product": "هذا المنتج غير متاح حاليًا أو تغيّر رابطه. ابحث في المنتجات أو انتقل إلى أحد الأقسام.",
    "category": "هذا القسم غير متاح حاليًا أو تغيّر رابطه. ابحث في المنتجات أو انتقل إلى الأقسام.",
    "page": "ربما تغيّر الرابط أو حُذفت الصفحة. ابحث في المنتجات أو انتقل إلى إحدى الصفحات التالية.",
}


def render_not_found(settings: dict, kind: str = "page") -> str:
    """Branded 404: search form (GET /products?q=) and the main destinations."""
    search = ('<form class="notfound-search" role="search" action="/products" method="get">'
              '<div class="form-field"><label for="notFoundSearch">ابحث في المنتجات</label>'
              '<input id="notFoundSearch" name="q" type="search" maxlength="100" placeholder="مثال: ستارة، رول، قماش" autocomplete="off"></div>'
              f'<button class="btn btn-primary" type="submit">{ICONS["search"]} بحث</button></form>')
    hero = page_hero([HOME_CRUMB, ("الصفحة غير موجودة", None)], eyebrow="خطأ 404", title="الصفحة غير موجودة",
                     intro_html=NOT_FOUND_INTRO.get(kind, NOT_FOUND_INTRO["page"]), extra=search)
    links = [("/", "الصفحة الرئيسية", "home"), ("/products", ALL_PRODUCTS, "bag"), ("/categories", "الأقسام", "grid"),
             ("/faq", "الأسئلة الشائعة", "help"), ("/contact", "تواصل معنا", "phone")]
    items = "".join(f'<li><a href="{href}"><span class="nf-icon" aria-hidden="true">{ICONS[icon]}</span>{label}{ARROW}</a></li>'
                    for href, label, icon in links)
    return (hero + '<section class="section notfound-section" aria-labelledby="notFoundLinksTitle"><div class="container">'
            '<h2 class="notfound-title" id="notFoundLinksTitle">روابط قد تفيدك</h2>'
            f'<ul class="notfound-links">{items}</ul></div></section>')


# -- shell ------------------------------------------------------------------------------------------------------------
MAIN_START, MAIN_END = "<!--main:start-->", "<!--main:end-->"
FAQ_START, FAQ_END = "<!--faq:start-->", "<!--faq:end-->"


def _swap(document: str, start: str, end: str, content: str) -> str:
    i, j = document.find(start), document.find(end)
    if i < 0 or j < i:
        raise RuntimeError(f"index.html is missing the {start} … {end} markers the server fills")
    return document[:i + len(start)] + "\n" + content + "\n" + document[j:]


def fill_home_faq(document: str) -> str:
    """The home page's FAQ block comes from FAQ too (single source)."""
    return _swap(document, FAQ_START, FAQ_END, render_faq_items())


CONSENT_START, CONSENT_END = "<!--consent:start-->", "<!--consent:end-->"
COOKIE_SETTINGS_MARK = "<!--cookie-settings-->"
COOKIE_SETTINGS_LABEL = "إعدادات ملفات التعريف"


def consent_banner(ga4_id: str) -> str:
    """Consent banner for the optional Google Analytics 4 (only rendered when the owner set a measurement ID).
    Hidden until the storefront script finds no stored choice; nothing from Google loads before «قبول»."""
    return (f'<section class="consent-banner" id="consentBanner" role="region" aria-labelledby="consentTitle" '
            f'aria-describedby="consentText" data-ga4-id="{esc(ga4_id)}" hidden>'
            '<div class="container consent-inner"><div class="consent-copy">'
            '<h2 id="consentTitle">ملفات تعريف الارتباط والإحصاءات</h2>'
            '<p id="consentText">نودّ استخدام Google Analytics لقياس الزيارات ومعرفة الصفحات الأكثر تصفحًا كي نحسّن المتجر. '
            'لا يُحمَّل إلا إذا ضغطت «قبول»، ويمكنك تغيير اختيارك في أي وقت من «' + COOKIE_SETTINGS_LABEL + '» أسفل الصفحة. '
            'التفاصيل في <a href="/privacy">سياسة الخصوصية</a>.</p>'
            '<p class="consent-current" id="consentCurrent" hidden></p></div>'
            '<div class="consent-actions" role="group" aria-label="اختيارك لملفات تعريف الارتباط">'
            '<button type="button" class="btn consent-btn" id="consentAccept">قبول</button>'
            '<button type="button" class="btn consent-btn" id="consentReject">رفض</button></div></div></section>')


def fill_consent(document: str, ga4_id: str) -> str:
    """With a GA4 ID: the consent banner and the footer «إعدادات ملفات التعريف» button. Without one: neither
    (the page stays exactly as before: no banner, no Google requests, the same CSP)."""
    out = _swap(document, CONSENT_START, CONSENT_END, consent_banner(ga4_id) if ga4_id else "")
    if COOKIE_SETTINGS_MARK not in out:
        raise RuntimeError(f"index.html is missing the {COOKIE_SETTINGS_MARK} marker the server fills")
    button = f'<button type="button" class="cookie-settings" id="cookieSettings" hidden>{COOKIE_SETTINGS_LABEL}</button>' if ga4_id else ""
    return out.replace(COOKIE_SETTINGS_MARK, button, 1)


def fill_shell(document: str, main: str, page: str, attrs: dict | None = None, *, nav_path: str = "", search: str = "") -> str:
    """Put a sub-page into the storefront shell: its <main> content, <body data-page …>, the page stylesheet,
    aria-current on the matching main-nav link and (for /products?q=) the header search value."""
    out = _swap(document, MAIN_START, MAIN_END, main)
    data = {"page": page, **(attrs or {})}
    body = "<body " + " ".join(f'data-{k}="{esc(v)}"' for k, v in data.items()) + ">"
    out, count = re.subn(r"<body\b[^>]*>", lambda _m: body, out, count=1)
    if count != 1:
        raise RuntimeError("index.html is missing its <body> tag")
    out = out.replace("</head>", f'<style id="pageStyles">{PAGE_CSS}</style>\n</head>', 1)
    if nav_path:
        def mark(m: re.Match) -> str:
            return re.sub(r'<a\b(?![^>]*aria-current)([^>]*\bhref="' + re.escape(nav_path) + '")',
                          lambda a: '<a aria-current="page"' + a.group(1), m.group(0))
        out = re.sub(r'<nav\b[^>]*\bid="mainNav"[^>]*>.*?</nav>', mark, out, count=1, flags=re.S)
    if search:
        out = re.sub(r'(<input\b[^>]*\bid="searchInput")', lambda m: f'{m.group(1)} value="{esc(search)}"', out, count=1)
    return out


# -- page stylesheet (injected once into <head>; uses the shell's tokens) --------------------------------------------
PAGE_CSS = """
/* pages:start — server-rendered sub-pages (pages.py). Night & gold, RTL, 390px-safe, reduced-motion safe. */
.page-hero{position:relative;isolation:isolate;overflow:hidden;background:radial-gradient(ellipse 55% 85% at 92% 0%,rgba(201,164,100,.26),transparent 62%),radial-gradient(ellipse 45% 70% at 0% 100%,rgba(201,164,100,.12),transparent 60%),var(--night);color:var(--ivory);padding:26px 0 70px}
.page-hero:after{content:"";position:absolute;right:6%;left:6%;bottom:0;height:1px;background:linear-gradient(90deg,transparent,rgba(234,214,166,.55),transparent);pointer-events:none}
.page-hero .container{position:relative;z-index:1}
.page-hero-media{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;z-index:-2}
.page-hero.has-media:before{content:"";position:absolute;inset:0;z-index:-1;background:linear-gradient(270deg,rgba(21,19,15,.95) 0%,rgba(21,19,15,.86) 46%,rgba(21,19,15,.5) 100%)}
.page-hero .eyebrow{color:var(--gold-2);margin-top:22px}
.page-hero h1{font-family:var(--display);font-weight:700;font-size:clamp(36px,5.2vw,64px);line-height:1.25;letter-spacing:0;margin:14px 0 12px;max-width:860px;color:var(--ivory);overflow-wrap:anywhere}
.page-hero-intro{margin:0;max-width:660px;font-size:17px;line-height:2;color:rgba(253,250,244,.86)}
.page-hero .btn-outline{color:var(--ivory);border-color:rgba(234,214,166,.45);background:rgba(253,250,244,.06)}
.page-hero .btn-outline:hover{background:rgba(253,250,244,.14);border-color:var(--gold-2)}
.page-hero--compact{background:var(--paper);color:var(--ink);padding:14px 0 6px}
.page-hero--compact:after{display:none}
.page-hero + .section,.category-switch-band + .section{padding-top:64px}
.breadcrumbs ol{list-style:none;margin:0;padding:0;display:flex;flex-wrap:wrap;align-items:center;column-gap:12px;font-size:14px}
.breadcrumbs li{display:inline-flex;align-items:center;gap:12px;min-width:0}
.breadcrumbs li+li:before{content:"";flex:0 0 auto;width:7px;height:7px;border-left:1.5px solid currentColor;border-bottom:1.5px solid currentColor;transform:rotate(45deg);opacity:.75}
.breadcrumbs a{display:inline-flex;align-items:center;min-height:44px;color:var(--gold-2);text-decoration:underline;text-decoration-color:rgba(234,214,166,.45);text-underline-offset:6px}
.breadcrumbs a:hover{text-decoration-color:currentColor}
.breadcrumbs [aria-current]{display:inline-flex;align-items:center;min-height:44px;color:rgba(253,250,244,.86);overflow-wrap:anywhere}
.page-hero--compact .breadcrumbs li+li:before{color:var(--gold-ink)}
.page-hero--compact .breadcrumbs a{color:var(--gold-ink);text-decoration-color:rgba(118,86,31,.4)}
.page-hero--compact .breadcrumbs [aria-current]{color:var(--muted)}
.page-more{margin:30px 0 0;text-align:center}
/* touch targets >= 44px for links/buttons this file adds or reuses */
.page-more .text-link,.about-services .text-link,.related-section .text-link{min-height:44px;padding-bottom:0}
.page-cta .contact-actions .btn{min-height:48px;font-size:14px}
.product-link{display:inline-flex;align-items:center;min-height:44px;color:inherit;text-decoration:none}
.product-link:hover{color:var(--gold-ink);text-decoration:underline;text-underline-offset:4px}
/* category switcher */
.category-switch-band{background:var(--paper);border-bottom:1px solid var(--line);padding:18px 0}
.category-switch ul{list-style:none;margin:0;padding:0;display:flex;flex-wrap:wrap;gap:8px}
.category-switch a{display:inline-flex;align-items:center;gap:6px;min-height:44px;padding:8px 18px;border-radius:999px;border:1px solid #d9cbb2;background:var(--white);color:var(--ink);font-size:14px;font-weight:600;transition:border-color .3s var(--ease),background .3s,color .3s}
.category-switch a:hover{border-color:var(--gold);color:var(--gold-ink)}
.category-switch a[aria-current="page"]{background:var(--night);border-color:var(--night);color:var(--gold-2)}
.category-switch .count{font-size:12px;font-weight:500;opacity:.85}
/* product page */
.product-page{padding:10px 0 80px}
.product-page-grid{display:grid;grid-template-columns:minmax(0,1.05fr) minmax(0,1fr);gap:52px;align-items:start}
.product-gallery{position:sticky;top:100px;display:grid;gap:12px;min-width:0}
.product-gallery-main{border-radius:28px;overflow:hidden;background:var(--sand);aspect-ratio:4/5;box-shadow:var(--shadow-soft);position:relative}
.product-gallery-main img{width:100%;height:100%;object-fit:cover}
.product-gallery-main:after{content:"";position:absolute;inset:12px;border:1px solid rgba(234,214,166,.5);border-radius:20px;pointer-events:none}
.product-gallery-thumbs{display:flex;flex-wrap:wrap;gap:10px}
.product-gallery-thumbs .gthumb{width:72px;height:72px;border-radius:14px}
.product-summary{display:grid;gap:14px;align-content:start;min-width:0;padding-top:6px}
.product-page-category{justify-self:start;display:inline-flex;align-items:center;min-height:44px;text-decoration:underline;text-decoration-color:rgba(118,86,31,.35);text-underline-offset:6px}
.product-summary h1{font-family:var(--display);font-weight:700;font-size:clamp(30px,3.6vw,46px);line-height:1.3;letter-spacing:0;margin:0;overflow-wrap:anywhere}
.product-page-price{margin:0;font-size:26px;font-weight:800;color:var(--gold-ink)}
.product-page-price small{font-size:15px;font-weight:500;color:var(--muted)}
.product-page-price .on-request{font-size:20px;color:var(--ink)}
.product-page-stock{display:inline-flex;align-items:center;gap:9px;margin:0;font-weight:700;font-size:15px;color:var(--ink)}
.product-page-stock:before{content:"";width:10px;height:10px;border-radius:50%;background:#2f6b3b;box-shadow:0 0 0 4px rgba(47,107,59,.14)}
.product-page-stock.is-out:before{background:#9a3b2f;box-shadow:0 0 0 4px rgba(154,59,47,.14)}
.product-page-desc p{margin:0 0 10px;color:var(--muted);font-size:16px;line-height:2}
.product-page-actions{display:flex;flex-wrap:wrap;gap:10px;margin-top:4px}
.product-page-actions .btn-primary{flex:1 1 230px}
.product-page-actions .btn[disabled]{opacity:.62;cursor:not-allowed;transform:none;box-shadow:none}
.product-page-notes{list-style:none;margin:6px 0 0;padding:18px 20px;border-radius:20px;background:var(--white);border:1px solid rgba(201,164,100,.28);display:grid;gap:8px;font-size:14.5px;line-height:1.9;color:var(--muted)}
.product-page-notes li{position:relative;padding-inline-start:20px}
.product-page-notes li:before{content:"";position:absolute;inset-inline-start:0;top:.75em;width:8px;height:8px;border-radius:50%;background:var(--gold-grad)}
.product-page-sku{margin:0;font-size:13px;color:var(--muted)}
.related-section{background:var(--cream)}
/* about */
.about-grid{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(0,.85fr);gap:56px;align-items:center}
.about-grid.no-figure{grid-template-columns:minmax(0,1fr);max-width:820px}
.prose p{margin:0 0 18px;font-size:17px;line-height:2.05;color:var(--ink)}
.prose p:first-of-type{font-family:var(--display);font-size:clamp(21px,2.3vw,27px);line-height:1.8;font-weight:600}
.about-figure{margin:0;position:relative;border-radius:30px;overflow:hidden;aspect-ratio:4/5;background:var(--sand);box-shadow:var(--shadow)}
.about-figure img{width:100%;height:100%;object-fit:cover}
.about-figure:after{content:"";position:absolute;inset:14px;border:1px solid rgba(234,214,166,.55);border-radius:20px;pointer-events:none}
.about-services{background:var(--cream)}
.service-card .text-link{margin-top:16px}
/* visit / contact info (dark card) */
.visit-card{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:34px;align-items:start;border-radius:32px;padding:50px 54px;color:var(--ivory);background:radial-gradient(ellipse 70% 120% at 0% 100%,rgba(201,164,100,.28),transparent 60%),var(--night);box-shadow:var(--shadow)}
.visit-card .eyebrow{color:var(--gold-2)}
.visit-card h2{font-family:var(--display);font-size:clamp(28px,3.4vw,42px);font-weight:700;line-height:1.3;margin:10px 0 8px;color:var(--ivory)}
.visit-copy p{margin:0;font-size:15.5px;line-height:2;color:rgba(253,250,244,.84)}
.info-list{list-style:none;margin:0;padding:0;display:grid;gap:16px}
.info-list li{display:grid;grid-template-columns:46px minmax(0,1fr);gap:14px;align-items:start}
.info-icon{width:46px;height:46px;border-radius:15px;display:grid;place-items:center;background:rgba(234,214,166,.1);color:var(--gold-2);box-shadow:inset 0 0 0 1px rgba(234,214,166,.32)}
.info-icon svg{width:20px;height:20px}
.info-label{display:block;font-size:13px;font-weight:700;color:var(--gold-2);margin-top:2px}
.info-value{font-size:15.5px;line-height:1.9;color:rgba(253,250,244,.92);overflow-wrap:anywhere}
.info-value a{display:inline-flex;align-items:center;min-height:44px;color:var(--ivory);text-decoration:underline;text-decoration-color:rgba(234,214,166,.5);text-underline-offset:6px}
.info-value a:hover{color:var(--gold-2)}
/* contact page */
.contact-layout{display:grid;grid-template-columns:minmax(0,.9fr) minmax(0,1.1fr);gap:28px;align-items:start}
.contact-info-card{grid-template-columns:minmax(0,1fr);padding:38px 36px;gap:26px}
.contact-form-card{background:var(--white);border:1px solid rgba(201,164,100,.24);border-radius:30px;padding:38px 36px;box-shadow:var(--shadow-soft);min-width:0}
.contact-form-card h2{font-family:var(--display);font-size:clamp(26px,3vw,36px);font-weight:700;margin:0 0 6px}
.contact-form-card>p{margin:0 0 22px;color:var(--muted);font-size:15px;line-height:1.9}
.contact-form .form-grid{gap:16px}
.contact-form .form-field label{font-size:14px;color:var(--ink)}
.contact-form .form-field input,.contact-form .form-field textarea{font-size:16px;min-height:50px;padding:12px 14px}
.contact-form .form-field textarea{min-height:150px}
.contact-form .consent{margin:18px 0;font-size:14.5px;color:var(--muted)}
.contact-form .consent a{color:var(--gold-ink);text-decoration:underline;text-underline-offset:4px}
.contact-form .btn{width:100%}
.form-status{margin:14px 0 0;min-height:1.6em;font-size:15px;font-weight:700;line-height:1.8;color:var(--ink)}
.form-status[data-state="success"]{color:#2f6b3b}
.form-status[data-state="error"]{color:#9a3b2f}
.contact-form-card .noscript-note{margin-top:16px;border-radius:14px}
.hp{position:absolute!important;width:1px;height:1px;margin:-1px;padding:0;overflow:hidden;clip:rect(0 0 0 0);clip-path:inset(50%);white-space:nowrap;border:0;opacity:0;pointer-events:none}
/* faq page */
.faq-page .faq-list{max-width:900px;margin-inline:auto}
/* call to action band */
.page-cta{padding-top:84px}
.page-cta .contact-copy p{max-width:520px}
/* 404 */
.notfound-search{display:flex;flex-wrap:wrap;align-items:flex-end;gap:12px;max-width:640px;margin-top:28px}
.notfound-search .form-field{flex:1 1 260px;min-width:0}
.notfound-search label{font-size:14px;color:var(--gold-2)}
.notfound-search input{width:100%;min-height:52px;border-radius:999px;border:1px solid transparent;background:var(--white);color:var(--ink);font-size:16px;padding:0 20px}
.notfound-search input:focus{border-color:var(--gold);box-shadow:0 0 0 4px rgba(201,164,100,.3)}
.notfound-search .btn{min-height:52px}
.notfound-search .btn svg{width:18px;height:18px}
.notfound-title{font-family:var(--display);font-size:clamp(24px,2.8vw,34px);margin:0 0 22px}
.notfound-links{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr));gap:14px}
.notfound-links a{display:flex;align-items:center;gap:14px;min-height:68px;padding:14px 18px;border-radius:20px;background:var(--white);border:1px solid rgba(201,164,100,.26);box-shadow:var(--shadow-soft);font-weight:700;color:var(--ink);transition:transform .4s var(--ease),border-color .3s}
.notfound-links a:hover{transform:translateY(-3px);border-color:var(--gold)}
.notfound-links a>svg{width:16px;height:16px;margin-inline-start:auto;color:var(--gold-ink)}
.nf-icon{width:40px;height:40px;flex:0 0 40px;border-radius:13px;display:grid;place-items:center;background:var(--night);color:var(--gold-2)}
.nf-icon svg{width:19px;height:19px}
@media (prefers-reduced-motion:no-preference){
  .page-hero .container>*{animation:rise .9s var(--ease) both}
  .page-hero .container>:nth-child(3){animation-delay:.08s}.page-hero .container>:nth-child(4){animation-delay:.16s}.page-hero .container>:nth-child(5){animation-delay:.24s}
}
@media (max-width:880px){
  .product-page-grid,.about-grid,.contact-layout,.visit-card{grid-template-columns:minmax(0,1fr)}
  .product-page-grid{gap:26px}.product-gallery{position:static}
  .about-grid{gap:30px}.about-figure{aspect-ratio:16/11;order:-1}
  .visit-card{padding:38px 32px}
}
@media (max-width:600px){
  .page-hero{padding:16px 0 46px}
  .page-hero h1{font-size:34px;line-height:1.3}
  .page-hero-intro{font-size:15px}
  .page-hero .eyebrow{margin-top:14px}
  .page-hero + .section,.category-switch-band + .section{padding-top:44px}
  .breadcrumbs ol{font-size:13px;column-gap:10px}
  .category-switch a{padding:8px 14px;font-size:13px}
  .product-gallery-main{border-radius:20px;aspect-ratio:1/1.05}
  .product-gallery-main:after{inset:8px;border-radius:14px}
  .product-gallery-thumbs .gthumb{width:60px;height:60px}
  .product-summary h1{font-size:28px}
  .product-page-price{font-size:22px}
  .product-page-actions .btn{flex:1 1 100%}
  .prose p{font-size:15.5px}
  .visit-card,.contact-info-card,.contact-form-card{padding:28px 20px;border-radius:24px}
  .info-list li{grid-template-columns:42px minmax(0,1fr);gap:12px}.info-icon{width:42px;height:42px}
  .page-cta{padding-top:60px}
  .notfound-search .btn{flex:1 1 100%}
}
/* pages:end */
"""
