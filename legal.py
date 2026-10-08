"""Privacy policy and terms pages, filled from the store settings. General templates: the owner should have them
reviewed against the shop's real practices before relying on them."""
from __future__ import annotations

from seo import esc

PAGES = {
    "/privacy": "سياسة الخصوصية",
    "/terms": "الشروط والأحكام",
}

_CSS = """
:root{--paper:#f8f6f0;--ink:#292720;--muted:#5f5d55;--line:#e2ddd1;--olive:#44503f}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font-family:system-ui,-apple-system,"Segoe UI",Tahoma,Arial,sans-serif;line-height:1.9;font-size:17px}
a{color:var(--olive)}a:focus-visible{outline:3px solid #b99b70;outline-offset:3px;border-radius:4px}
header{border-bottom:1px solid var(--line);background:#fffefa}.wrap{width:min(780px,calc(100% - 32px));margin:0 auto}
header .wrap{display:flex;align-items:center;justify-content:space-between;gap:12px;min-height:64px}
header a.brand{font-weight:800;text-decoration:none;color:var(--ink);font-size:19px}
main{padding:32px 0 56px}h1{font-size:30px;margin:0 0 8px;line-height:1.3}h2{font-size:21px;margin:30px 0 6px}
p,li{margin:6px 0}ul{padding-inline-start:22px}.meta{color:var(--muted);font-size:15px}
.skip{position:absolute;inset-inline-start:8px;top:-60px;background:#fff;padding:10px 14px;border-radius:8px;z-index:9}.skip:focus{top:8px}
footer{border-top:1px solid var(--line);padding:20px 0;color:var(--muted);font-size:15px}
"""


def _contact(s: dict) -> str:
    bits = []
    if s.get("phone"):
        bits.append(f'الهاتف: <a href="tel:{esc("".join(c for c in s["phone"] if c.isdigit() or c == "+"))}" dir="ltr">{esc(s["phone"])}</a>')
    if s.get("address"):
        bits.append(f"العنوان: {esc(s['address'])}")
    return "<br>".join(bits) or "عبر قنوات التواصل المعلنة في المتجر"


def _privacy(s: dict, store: str) -> str:
    return f"""
<h2>١. من نحن</h2>
<p>{esc(store)} متجر لبيع الأقمشة والستائر وتفصيلها. هذه الصفحة توضّح كيف نتعامل مع بياناتك عند استخدام الموقع أو التطبيق.</p>
<p>{_contact(s)}</p>
<h2>٢. البيانات التي نجمعها</h2>
<ul>
<li><b>عند إرسال طلب:</b> الاسم، رقم الجوال، المدينة، العنوان (إن أدخلته)، ملاحظتك، والمنتجات والكميات.</li>
<li><b>على جهازك فقط:</b> السلة والمفضلة تُحفظ في ذاكرة المتصفح/التطبيق على جهازك ولا تُرسل إلينا إلا عند إرسال الطلب.</li>
<li>لا نجمع بيانات بطاقات أو حسابات دفع؛ لا يوجد دفع إلكتروني في المتجر حاليًا.</li>
<li>لا نستخدم ملفات تعريف ارتباط للتتبع أو للإعلانات. تُستخدم ملفات الجلسة الضرورية لمدير المتجر فقط.</li>
</ul>
<h2>٣. لماذا نستخدم بياناتك</h2>
<p>لمعالجة طلبك والتواصل معك لتأكيد المقاس والخامة والسعر والتوصيل، ولخدمة ما بعد التنفيذ.</p>
<h2>٤. المشاركة</h2>
<p>لا نبيع بياناتك. لا نشاركها إلا بالقدر اللازم لتنفيذ طلبك (مثل جهة توصيل إن لزم) أو متى ألزمتنا الأنظمة بذلك.</p>
<h2>٥. مدة الاحتفاظ</h2>
<p>نحتفظ ببيانات الطلب بالقدر اللازم لتنفيذه وخدمة ما بعده والوفاء بالالتزامات النظامية، ثم تُحذف أو تُجهَّل.</p>
<h2>٦. حقوقك</h2>
<p>يمكنك طلب الاطلاع على بياناتك أو تصحيحها أو حذفها أو الاعتراض على معالجتها بالتواصل معنا عبر بيانات التواصل أعلاه، وسنرد وفق الأنظمة المعمول بها في المملكة العربية السعودية.</p>
<h2>٧. الأمان</h2>
<p>نتخذ إجراءات تقنية معقولة لحماية البيانات، منها الاتصال المشفر (HTTPS) وحماية لوحة الإدارة بكلمة مرور وجلسات آمنة، والتخزين على خادم المتجر. لا يوجد نظام محصّن بالكامل، لذا ننصح بعدم إرسال معلومات حساسة في خانة الملاحظات.</p>
<h2>٨. التحديث</h2>
<p>قد نحدّث هذه السياسة، ويُعرض أحدث إصدار في هذه الصفحة.</p>
"""


def _terms(s: dict, store: str) -> str:
    return f"""
<h2>١. طبيعة الطلب</h2>
<p>إرسال الطلب عبر الموقع أو التطبيق هو <b>طلب للمراجعة</b> وليس عقدًا نهائيًا؛ يتواصل معك {esc(store)} لتأكيد التفاصيل قبل اعتماد التنفيذ.</p>
<h2>٢. الأسعار</h2>
<p>الأسعار بالريال السعودي. المنتجات المعروضة بعبارة «السعر حسب الطلب» (كالستائر المفصّلة) يُحدَّد سعرها بعد معرفة المقاس والخامة واللون وتأكيده معك قبل البدء.</p>
<h2>٣. التفصيل والمقاسات</h2>
<p>تُفصَّل الستائر حسب المقاسات المؤكدة. يتحمّل العميل صحة المقاسات التي يقدّمها ما لم يتولّ المتجر القياس، وتُؤكَّد المدة المتوقعة للتجهيز عند اعتماد الطلب.</p>
<h2>٤. الدفع والتوصيل والتركيب</h2>
<p>لا يوجد دفع إلكتروني في الموقع حاليًا؛ تُحدَّد طريقة الدفع والتوصيل والتركيب ورسومها عند تأكيد الطلب مع المتجر.</p>
<h2>٥. الاستبدال والاسترجاع</h2>
<p>تُحدَّد سياسة الاستبدال والاسترجاع للأقمشة والمنتجات الجاهزة والمفصّلة وتُعلَن من المتجر عند تأكيد الطلب، وبما يتوافق مع الأنظمة المعمول بها.</p>
<h2>٦. الصور والمحتوى</h2>
<p>الصور للتوضيح، وقد تختلف الألوان قليلًا عن الواقع بحسب الشاشة والإضاءة. محتوى الموقع مملوك للمتجر ولا يجوز نسخه دون إذن.</p>
<h2>٧. التواصل</h2>
<p>{_contact(s)}</p>
<p class="meta">راجع أيضًا <a href="/privacy">سياسة الخصوصية</a>.</p>
"""


def render(path: str, settings: dict) -> str | None:
    """Full HTML document (inline <style> only; the server adds the CSP nonce) or None for an unknown path."""
    if path not in PAGES:
        return None
    store = settings.get("store_name") or "المتجر"
    title = f"{PAGES[path]} | {store}"
    body = (_privacy if path == "/privacy" else _terms)(settings, store)
    return f"""<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#f5f1e9">
<title>{esc(title)}</title>
<meta name="description" content="{esc(PAGES[path])} — {esc(store)}">
<link rel="icon" href="/icons/icon-192.png" sizes="192x192" type="image/png">
<style>{_CSS}</style>
</head>
<body>
<a class="skip" href="#content">تخطي إلى المحتوى</a>
<header><div class="wrap"><a class="brand" href="/">{esc(store)}</a><a href="/">العودة للمتجر</a></div></header>
<main id="content"><div class="wrap"><h1>{esc(PAGES[path])}</h1><p class="meta">{esc(store)}</p>{body}</div></main>
<footer><div class="wrap"><a href="/privacy">سياسة الخصوصية</a> · <a href="/terms">الشروط والأحكام</a> · <a href="/">المتجر</a></div></footer>
</body>
</html>"""
