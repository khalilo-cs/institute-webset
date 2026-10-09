"""Privacy policy and terms pages, filled from the store settings. General templates: the owner should have them
reviewed against the shop's real practices before relying on them."""
from __future__ import annotations

from seo import esc, ga4_id, verification_meta

PAGES = {
    "/privacy": "سياسة الخصوصية",
    "/terms": "الشروط والأحكام",
}

_CSS = """
:root{--paper:#f8f4ec;--ink:#1b1915;--muted:#5d574c;--line:#e6dccb;--olive:#76561f}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font-family:system-ui,-apple-system,"Segoe UI",Tahoma,Arial,sans-serif;line-height:1.9;font-size:17px}
a{color:var(--olive)}a:focus-visible{outline:3px solid #76561f;outline-offset:3px;border-radius:4px}
header{border-bottom:2px solid #c9a464;background:#15130f}header a{color:#ead6a6}header a:focus-visible{outline-color:#ead6a6}.wrap{width:min(780px,calc(100% - 32px));margin:0 auto}
header .wrap{display:flex;align-items:center;justify-content:space-between;gap:12px;min-height:64px}
header a.brand{font-weight:800;text-decoration:none;color:#fdfaf4;font-size:19px}
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
    bits.append('أو عبر <a href="/contact">صفحة التواصل</a>')
    return "<br>".join(bits)


_DIGITS = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")


def _numbered(sections: list[tuple[str, str]]) -> str:
    """<h2>١. title</h2> + body for each section, numbered in order (the GA section is optional)."""
    return "\n".join(f"<h2>{str(i).translate(_DIGITS)}. {title}</h2>\n{body}" for i, (title, body) in enumerate(sections, 1))


def _google_analytics(store: str) -> str:
    """Only when the owner set a GA4 measurement ID: who processes what, and how to withdraw consent."""
    return f"""<p>إذا ضغطت «قبول» في شريط ملفات تعريف الارتباط، يحمّل الموقع خدمة Google Analytics 4 لنعرف كيف يُستخدم المتجر (مثل الصفحات التي تُزار ومصدر الزيارة) فنحسّنه. إن ضغطت «رفض» أو لم تختر شيئًا فلا تُحمَّل الخدمة ولا يُرسل إليها شيء.</p>
<ul>
<li><b>من يعالج البيانات:</b> شركة Google بصفتها مقدّمة الخدمة، لصالح {esc(store)} الذي يملك حساب Analytics. تخضع معالجة Google لـ<a href="https://policies.google.com/privacy">سياسة خصوصية Google</a>، وتشرح Google <a href="https://policies.google.com/technologies/partner-sites">كيف تستخدم بيانات المواقع التي تستخدم خدماتها</a>.</li>
<li><b>ما الذي يُجمع:</b> الصفحات التي تزورها ووقت الزيارة ومدتها، والموقع أو الرابط الذي جئت منه، ونوع الجهاز والمتصفح ونظام التشغيل، والبلد أو المدينة التقريبية التي تستنتجها Google من عنوان IP، ومعرّف عشوائي يُحفظ في ملفات تعريف الارتباط <span dir="ltr">_ga</span> و<span dir="ltr">_ga_*</span> على جهازك. لا نرسل إلى Google اسمك أو رقم جوالك أو بيانات طلبك أو رسائلك.</li>
<li><b>الإعلانات:</b> عطّلنا في إعداد الخدمة إشارات Google (Google Signals) وتخصيص الإعلانات، ولا نستخدم هذه البيانات للإعلان.</li>
<li><b>المدة:</b> تبقى ملفات Google Analytics على جهازك حتى سنتين ما لم تحذفها أو تسحب موافقتك، وتحتفظ Google بالبيانات حسب مدة الاحتفاظ المضبوطة في حساب Analytics.</li>
<li><b>سحب الموافقة:</b> اضغط «إعدادات ملفات التعريف» أسفل أي صفحة من صفحات المتجر واختر «رفض»؛ يتوقف الإرسال فورًا وتُحذف ملفات <span dir="ltr">_ga</span> التي حفظها هذا الموقع. يمكنك أيضًا حذف ملفات تعريف الارتباط من إعدادات متصفحك، أو استخدام <a href="https://tools.google.com/dlpage/gaoptout">إضافة إيقاف Google Analytics</a>.</li>
</ul>"""


def _privacy(s: dict, store: str) -> str:
    ga = bool(ga4_id(s))
    cookies = ("لا نستخدم ملفات تعريف ارتباط للإعلانات. ملفات Google Analytics لا تُحفظ إلا بعد موافقتك (انظر البند التالي)، "
               "ويُحفظ اختيارك في شريط الموافقة على جهازك. تُستخدم ملفات الجلسة الضرورية لمدير المتجر فقط."
               if ga else "لا نستخدم ملفات تعريف ارتباط للتتبع أو للإعلانات. تُستخدم ملفات الجلسة الضرورية لمدير المتجر فقط.")
    sections = [
        ("من نحن", f"""<p>{esc(store)} متجر لبيع الأقمشة والستائر وتفصيلها. هذه الصفحة توضّح كيف نتعامل مع بياناتك عند استخدام الموقع أو التطبيق.</p>
<p>{_contact(s)}</p>"""),
        ("البيانات التي نجمعها", f"""<ul>
<li><b>عند إرسال طلب:</b> الاسم، رقم الجوال، المدينة، العنوان (إن أدخلته)، ملاحظتك، والمنتجات والكميات.</li>
<li><b>عند إرسال رسالة من صفحة التواصل:</b> الاسم، رقم الجوال، البريد الإلكتروني (إن أدخلته)، نص رسالتك، والصفحة التي أرسلتها منها، ووقت موافقتك على هذه السياسة. لا نحفظ عنوان IP مع الرسالة في قاعدة البيانات، ونستخدمه مؤقتًا للحد من الإرسال المتكرر. قد يظهر عنوان IP في سجلات الخادم التقنية (Access logs) التي تُحفظ لفترة محدودة لأغراض الأمان وتشخيص الأعطال فقط.</li>
<li><b>على جهازك فقط:</b> السلة والمفضلة تُحفظ في ذاكرة المتصفح/التطبيق على جهازك ولا تُرسل إلينا إلا عند إرسال الطلب.</li>
<li><b>إحصاءات الزيارات:</b> نحصي في خادم المتجر عدد مرات عرض كل صفحة يوميًا واسم الموقع الذي جاء منه الزائر (إن وُجد) كأرقام مجمّعة فقط، دون ملفات تعريف ارتباط ودون حفظ عنوان IP أو نوع الجهاز أو أي بيانات تعرّف بك، وتُحذف هذه الأرقام بعد 400 يوم.</li>
<li>لا نجمع بيانات بطاقات أو حسابات دفع؛ لا يوجد دفع إلكتروني في المتجر حاليًا.</li>
<li>{cookies}</li>
</ul>"""),
    ]
    if ga:
        sections.append(("Google Analytics (بموافقتك فقط)", _google_analytics(store)))
    sections += [
        ("لماذا نستخدم بياناتك", """<p>لمعالجة طلبك والتواصل معك لتأكيد المقاس والخامة والسعر والتوصيل، ولخدمة ما بعد التنفيذ.</p>
<p>بيانات رسائل صفحة التواصل تُستخدم فقط للرد على استفسارك ومتابعته، ولا تُستخدم للتسويق.</p>"""),
        ("المشاركة", "<p>لا نبيع بياناتك. لا نشاركها إلا بالقدر اللازم لتنفيذ طلبك (مثل جهة توصيل إن لزم) أو متى ألزمتنا الأنظمة بذلك"
                     + ("، ويعالج Google بيانات الزيارة الموضحة أعلاه إن وافقت على Google Analytics" if ga else "") + ".</p>"),
        ("مدة الاحتفاظ", """<p>نحتفظ ببيانات الطلب بالقدر اللازم لتنفيذه وخدمة ما بعده والوفاء بالالتزامات النظامية، ثم تُحذف أو تُجهَّل.</p>
<p>نحتفظ برسائل صفحة التواصل بالقدر اللازم للرد عليها ومتابعتها، ثم تُحذف من لوحة إدارة المتجر.</p>"""),
        ("حقوقك", "<p>يمكنك طلب الاطلاع على بياناتك أو تصحيحها أو حذفها أو الاعتراض على معالجتها بالتواصل معنا عبر بيانات التواصل أعلاه، وسنرد وفق الأنظمة المعمول بها في المملكة العربية السعودية.</p>"),
        ("الأمان", "<p>نتخذ إجراءات تقنية معقولة لحماية البيانات، منها الاتصال المشفر (HTTPS) وحماية لوحة الإدارة بكلمة مرور وجلسات آمنة، والتخزين على خادم المتجر. لا يوجد نظام محصّن بالكامل، لذا ننصح بعدم إرسال معلومات حساسة في خانة الملاحظات أو في رسائل صفحة التواصل.</p>"),
        ("التحديث", "<p>قد نحدّث هذه السياسة، ويُعرض أحدث إصدار في هذه الصفحة.</p>"),
    ]
    return "\n" + _numbered(sections) + "\n"


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
<meta name="theme-color" content="#15130f">
<title>{esc(title)}</title>
<meta name="description" content="{esc(PAGES[path])} — {esc(store)}">
<link rel="icon" href="/icons/icon-192.png" sizes="192x192" type="image/png">{verification_meta(settings)}
<style>{_CSS}</style>
</head>
<body>
<a class="skip" href="#content">تخطي إلى المحتوى</a>
<header><div class="wrap"><a class="brand" href="/">{esc(store)}</a><a href="/">العودة للمتجر</a></div></header>
<main id="content"><div class="wrap"><h1>{esc(PAGES[path])}</h1><p class="meta">{esc(store)}</p>{body}</div></main>
<footer><div class="wrap"><a href="/privacy">سياسة الخصوصية</a> · <a href="/terms">الشروط والأحكام</a> · <a href="/">المتجر</a></div></footer>
</body>
</html>"""
