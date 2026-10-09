# توثيق واجهة البرمجة (API)

كل مسارات الخادم `app.py` العامة والإدارية: الطريقة، والمصادقة وCSRF، والطلب والاستجابة بأمثلة حقيقية مأخوذة من خادم تجريبي، والأخطاء، وحدود المحاولات. الموقع ولوحة الإدارة وتطبيق Android يستخدمون هذه الواجهة نفسها.

## اصطلاحات عامة

- **العنوان الأساسي:** `https://<نطاق-المتجر>` (محليًا `http://127.0.0.1:4173`).
- **الصيغة:** JSON بترميز UTF-8. أرسل `Content-Type: application/json` مع أي جسم طلب.
- **الأخطاء:** دائمًا بالشكل `{"error": "<رسالة عربية للمستخدم>"}`، وقد تُضاف حقول مثل `need_code`.
- **رؤوس كل استجابة:** `X-Request-ID` (معرّف للربط بسجل الخادم)، و`Cache-Control: no-store` لواجهة API ما لم يُذكر غير ذلك، ورؤوس الأمان (CSP في صفحات HTML، و`X-Frame-Options: DENY`، و`X-Content-Type-Options: nosniff`…). مسارات `/api/admin/*` تحمل `X-Robots-Tag: noindex, nofollow`.
- **الضغط:** gzip للاستجابات النصية من 800 بايت فأكثر حين يرسل العميل `Accept-Encoding: gzip`.
- **الطلبات الشرطية:** نقاط الكتالوج العامة (`/api/store` و`/api/categories` و`/api/products`) تحمل `ETag` و`Cache-Control: no-cache`؛ أرسل `If-None-Match` لتحصل على `304` دون جسم إن لم يتغير شيء.
- **`HEAD`** مدعوم لكل مسارات القراءة.

### المصادقة وCSRF

1. `POST /api/admin/login` يعيد `csrf` في الجسم ويضبط كوكيين: `fakhama_admin` (رمز الجلسة: `HttpOnly; SameSite=Lax; Max-Age=43200` و`Secure` خلف HTTPS) و`fakhama_admin_flag=1` (علامة غير سرية تستخدمها الواجهة لإظهار شريط المدير).
2. **القراءة الإدارية (`GET`)** تتطلب كوكي الجلسة فقط.
3. **كل كتابة إدارية (`POST`/`PUT`/`PATCH`/`DELETE`)** تتطلب كوكي الجلسة **و**ترويسة `X-CSRF-Token: <csrf>`. يمكن استعادة الرمز من `GET /api/admin/me`.
4. الجلسة صالحة 12 ساعة، ومحفوظة في ذاكرة الخادم (تنتهي عند إعادة تشغيله)، وتغيير كلمة المرور يُنهي الجلسات الأخرى.

| الحالة | المعنى | النص |
|---|---|---|
| `401` | لا توجد جلسة صالحة | «يلزم تسجيل الدخول للمتابعة.» |
| `403` | رمز CSRF مفقود أو خاطئ | «انتهت صلاحية الجلسة. حدّث الصفحة ثم سجّل الدخول مجددًا.» |

### حدود المحاولات (Rate limits)

العدّادات في ذاكرة الخادم لكل عنوان IP للعميل (خلف الوكيل مع `TRUST_PROXY=1`: آخر قيمة في `X-Forwarded-For` التي يضيفها الوكيل نفسه)، بنافذة منزلقة.

| المسار | الحد | الاستجابة عند التجاوز |
|---|---|---|
| `POST /api/admin/login` | 8 محاولات **فاشلة** (كلمة مرور أو رمز 2FA) خلال 5 دقائق | `429` + `Retry-After: 300` — «محاولات كثيرة. انتظر بضع دقائق ثم حاول مرة أخرى.» |
| `POST /api/orders` | 10 محاولات كل 10 دقائق (`ORDER_LIMIT_PER_10MIN`)؛ تُحسب المحاولة قبل التحقق من البيانات، والإعادة بمفتاح `Idempotency-Key` معروف لا تُحسب | `429` + `Retry-After: 600` |
| `POST /api/contact` | 5 رسائل **مقبولة** كل 10 دقائق (`CONTACT_LIMIT_PER_10MIN`) | `429` + `Retry-After: 600` |

### حدود حجم الطلب

| النوع | الحد | الخطأ |
|---|---|---|
| جسم JSON عادي | 512 KB | `413` «الطلب أكبر من الحد المسموح.» |
| المنتجات والأقسام والرفع (صور base64) | 9 MB للطلب، و5 MB لكل صورة بعد فك الترميز | `413` |
| نموذج التواصل | 32 KB | `413` |
| JSON غير صالح أو ليس كائنًا | — | `400` «تعذر قراءة بيانات الطلب.» / «صيغة بيانات الطلب غير صالحة.» |

---

## المسارات العامة

### `GET /api/health`

فحص الخادم وقاعدة البيانات (يستخدمه `HEALTHCHECK` في Docker والمراقبة الخارجية). بلا مصادقة.

```json
{"ok": true, "service": "fakhama-store", "version": "2.0.0", "db": true, "time": "2026-10-09T18:28:23+00:00"}
```

عند تعذّر الوصول إلى قاعدة البيانات: `503` و`{"ok": false, "service": "fakhama-store", "version": "2.0.0", "db": false}`.

### `GET /api/store`

بيانات المتجر العامة التي تعرضها الواجهة. بلا مصادقة، مع `ETag`.

```json
{
  "name": "الفخامة للأقمشة والستائر", "tagline": "تفصيل ستائر وأقمشة في جدة", "city": "جدة",
  "phone": "+966 57 648 6491", "whatsapp": "",
  "address": "شارع المكرونة، مجمع الشرق، حي مشرفة، جدة 23444",
  "map_url": "https://maps.app.goo.gl/kn2kxuPujQT9raLG7?g_st=awb",
  "delivery_fee": 0, "delivery_note": "تفصيل الستائر حسب المقاس؛ …", "site_ready": false,
  "seo_title": "…", "seo_description": "…", "hero_title": "ستائر تكمّل أناقة بيتك.", "hero_copy": "…",
  "about_title": "من نحن", "opening_hours": ""
}
```

### `GET /api/categories`

الأقسام **النشطة** مرتبة حسب `sort_order` (تشمل الأقسام النشطة التي لا منتجات فيها؛ الواجهة تعرض فقط ما فيه منتجات). بلا مصادقة، مع `ETag`.

```json
{"categories": [
  {"slug": "curtains", "name": "ستائر تفصيل", "sort_order": 1, "image": "", "description": ""},
  {"slug": "roller", "name": "ستائر رول", "sort_order": 2, "image": "", "description": ""}
]}
```

### `GET /api/products`

المنتجات الظاهرة في أقسام نشطة، المميزة أولًا ثم الأحدث، مع معرض الصور. بلا مصادقة، مع `ETag`. `price: 0` تعني «السعر حسب الطلب».

```json
{"products": [{
  "images": [{"url": "/assets/wavy-03.jpg", "thumb": "/assets/wavy-03-t.jpg"}],
  "thumb": "/assets/wavy-03-t.jpg", "id": 16, "name": "ستائر ويفي تفصيل حسب الطلب — موديل 3",
  "slug": "wavy-03", "category": "curtains", "category_name": "ستائر تفصيل",
  "description": "ستائر ويفي بطيّات متموجة تُفصَّل حسب الطلب. …", "price": 0,
  "image": "/assets/wavy-03.jpg", "alt": "ستائر ويفي كريمية ورمادية مع شيفون في صالة بإضاءة مخفية وثريا",
  "badge": "تفصيل حسب الطلب", "sku": "FAL-WAV-003", "stock": 999, "featured": true, "active": true,
  "created_at": "2026-10-09T18:28:22+00:00", "updated_at": "2026-10-09T18:28:22+00:00"
}]}
```

### `POST /api/orders`

إنشاء طلب (عام، بلا مصادقة). يحسب الخادم الأسعار ورسوم التوصيل من قاعدة البيانات ويخصم المخزون في معاملة واحدة.

**ترويسة اختيارية:** `Idempotency-Key` من 8 إلى 64 حرفًا (`A-Z a-z 0-9 _ -`). إعادة الإرسال بالمفتاح نفسه خلال 10 دقائق تعيد النتيجة الأولى بالحالة `200` مع `Idempotent-Replayed: true` ولا تُنشئ طلبًا ثانيًا. المفتاح غير الصالح: `400` «مفتاح التكرار غير صالح.».

**الطلب:**

```json
{"name": "عميل تجريبي", "phone": "0500000000", "city": "جدة", "address": "", "note": "",
 "consent": true, "items": [{"product_id": 16, "quantity": 2}]}
```

| الحقل | القواعد |
|---|---|
| `consent` | يجب أن يكون `true` حرفيًا |
| `name` | حرفان على الأقل (يُقتطع عند 120) |
| `phone` | من 8 إلى 15 رقمًا بعد إزالة غير الأرقام (يُقتطع النص عند 30) |
| `city` | حرفان على الأقل (100) |
| `address` | اختياري (250)؛ `note` أو `notes` اختياري (1000) |
| `items` | من 1 إلى 40 بندًا؛ لكل بند `product_id` (أو `id`) و`quantity` (أو `qty`) من 1 إلى 50؛ البنود المكررة للمنتج نفسه تُجمع |

**الاستجابة `201`:**

```json
{"ok": true, "order_id": 1, "order_number": "MH-261009-00001", "subtotal": 0, "delivery_fee": 0, "total": 0}
```

**الأخطاء:** `400` (مثل «يلزم الموافقة على سياسة الخصوصية لإرسال الطلب.»، «اكتب الاسم كاملًا.»، «تحقق من رقم الجوال.»، «اكتب المدينة.»، «أضف منتجًا واحدًا على الأقل إلى السلة.»)، `409` («أحد المنتجات لم يعد متاحًا. حدّث الصفحة وحاول مجددًا.» أو «الكمية المتاحة من «…» هي N فقط.»)، `413`، `429`.

### `POST /api/contact`

رسالة من نموذج «تواصل معنا» (عام، بلا مصادقة). يقبل:

- `application/json` من سكربت الصفحة ← `201 {"ok": true}`.
- `application/x-www-form-urlencoded` من النموذج نفسه حين يكون JavaScript متوقفًا ← يعيد صفحة «تواصل معنا» (HTML) برسالة النجاح أو الخطأ وبحالة الخطأ نفسها.
- أي نوع محتوى آخر ← `415` «صيغة الطلب غير مدعومة.».

**حماية المصدر:** طلب يحمل `Sec-Fetch-Site: cross-site` أو ترويسة `Origin` لموقع آخر يُرفض بـ `403` «الطلب من مصدر غير مسموح.».

**الطلب:**

```json
{"name": "عميل تجريبي", "phone": "0500000000", "email": "", "message": "رسالة تجريبية",
 "page": "/contact", "consent": true, "website": ""}
```

| الحقل | القواعد |
|---|---|
| `name` | 2–80 حرفًا |
| `phone` | 8–16 رقمًا بعد التنظيف (تُقبل الأرقام العربية الهندية، وتُحفظ `+` في البداية) |
| `email` | اختياري؛ عنوان بريد بسيط حتى 120 حرفًا |
| `message` | 5–2000 حرف |
| `consent` | `true` (في النموذج: `1` أو `on` أو `true` أو `yes`) |
| `page` | اختياري: مسار داخلي يبدأ بـ `/` وإلا يُحفظ فارغًا |
| `website` | حقل مخفي (honeypot): إن مُلئ تُرد `201` ولا يُحفظ شيء |

**الأخطاء:** `400` (مثل «اكتب اسمك (حرفان على الأقل).»، «تحقق من رقم الجوال (من 8 إلى 16 رقمًا).»، «يلزم الموافقة على سياسة الخصوصية لإرسال الرسالة.»)، `403`، `413`، `415`، `429`.

### `GET /robots.txt` و`GET /sitemap.xml`

- قبل تفعيل `site_ready`: `robots.txt` = `User-agent: *` + `Disallow: /`، و`sitemap.xml` يعيد `404`.
- بعد التفعيل: `robots.txt` يسمح بكل شيء عدا `/admin` و`/api/` ويشير إلى الخريطة؛ و`sitemap.xml` يضم الرئيسية وصفحات المتجر والقانونية والأقسام التي فيها منتجات وكل منتج مع صورته وتاريخ آخر تعديل.

---

## مسارات الإدارة

جميعها تحت `/api/admin/` وتتطلب جلسة؛ والكتابة تتطلب `X-CSRF-Token` أيضًا (انظر «المصادقة وCSRF»). المسار غير المعروف يعيد `404` «المسار غير موجود.» (للقراءة) أو «المسار أو الطريقة غير مدعومين.» (للكتابة).

### الجلسة

#### `POST /api/admin/login`

بلا جلسة وبلا CSRF. الجسم: `{"username": "admin", "password": "…", "code": "123456"}` (`code` فقط عند تفعيل التحقق بخطوتين).

```json
{"ok": true, "username": "admin", "csrf": "<رمز CSRF>"}
```

مع `Set-Cookie: fakhama_admin=…; Path=/; HttpOnly; SameSite=Lax; Max-Age=43200` و`Set-Cookie: fakhama_admin_flag=1; Path=/; SameSite=Lax; Max-Age=43200`.

| الحالة | المعنى |
|---|---|
| `401` | «اسم المستخدم أو كلمة المرور غير صحيحة.» |
| `401` + `"need_code": true` | «أدخل رمز التحقق من تطبيق المصادقة.» (لم يُرسل `code`) أو «رمز التحقق غير صحيح أو منتهٍ.» |
| `429` | بعد 8 إخفاقات خلال 5 دقائق، مع `Retry-After: 300` |

كل دخول ناجح أو فاشل يُسجَّل في سجل التدقيق (دون كلمة المرور).

#### `POST /api/admin/logout`

جلسة + CSRF. يحذف الجلسة ويمسح الكوكيين: `{"ok": true}`.

#### `GET /api/admin/me`

```json
{"authenticated": true, "username": "admin", "csrf": "<رمز CSRF>", "totp_enabled": false}
```

### لوحة المعلومات والإحصاءات والسجل

#### `GET /api/admin/dashboard`

```json
{"new_messages": 1, "products": 18, "active_products": 18, "pending_orders": 1, "total_orders": 1,
 "delivered_revenue": 0, "low_stock": 0,
 "recent_orders": [{"id": 1, "order_number": "MH-261009-00001", "customer_name": "عميل تجريبي", "phone": "0500000000",
   "city": "جدة", "status": "new", "status_label": "جديد", "total": 0, "on_request": true, "created_at": "…", "…": "…"}],
 "daily": [{"date": "2026-10-08", "orders": 0, "total": 0}, {"date": "2026-10-09", "orders": 1, "total": 0}],
 "low_stock_items": []}
```

`recent_orders`: آخر 6 طلبات دون بنودها. `daily`: 14 يومًا (UTC) للطلبات غير الملغاة. `low_stock`: المنتجات الظاهرة التي مخزونها ≤ 3 (حتى 8 في `low_stock_items`). `on_request: true` حين يحوي الطلب بندًا بسعر 0.

#### `GET /api/admin/analytics?days=7|30|90`

الافتراضي 30؛ أي قيمة أخرى `400` «الفترة غير مدعومة؛ اختر 7 أو 30 أو 90 يومًا.».

```json
{"days": 7, "timezone": "Asia/Riyadh",
 "daily": [{"date": "2026-10-08", "views": 0}, {"date": "2026-10-09", "views": 0}],
 "period_views": 0, "totals": {"today": 0, "last7": 0, "last30": 0},
 "top_pages": [{"path": "/", "views": 12}], "top_products": [{"slug": "wavy-03", "name": "…", "views": 3}],
 "referrers": [{"host": "direct", "views": 10}], "orders_last30": 1, "contact_last30": 1}
```

`daily` مرتبة من الأقدم ومملوءة بالأصفار؛ القوائم الثلاث حتى 10 عناصر لكل منها.

#### `GET /api/admin/audit`

آخر 100 عملية: `{"entries": [{"at": "…", "actor": "admin", "action": "category_change", "detail": "POST /api/admin/categories", "ip": "127.0.0.1"}]}`.

العمليات المسجّلة: `login`، `login_failed`، `logout`، `product_create`، `product_update`، `product_delete`، `category_change`، `settings_update`، `order_status`، `message_status`، `message_delete`، `password_change`، `two_factor`، `backup_download`.

### المنتجات والصور

#### `GET /api/admin/products`

كل المنتجات (الظاهرة والمخفية)، الأحدث أولًا، بالشكل نفسه في `GET /api/products`: `{"products": [ … ]}`.

#### `POST /api/admin/products` و`PUT /api/admin/products/<id>`

جلسة + CSRF، حتى 9 MB. الإنشاء `201`، والتعديل `200`، وكلاهما `{"ok": true, "product": { … }}`.

```json
{"name": "ستائر ويفي كريمية", "category": "curtains", "price": 0, "stock": 999, "sku": "FAL-WAV-020",
 "badge": "جديد", "description": "…", "alt": "…", "active": true, "featured": false,
 "images": [{"url": "/uploads/8b5e….jpg", "thumb": "/uploads/1c2d….jpg"}],
 "new_images": [{"data": "data:image/jpeg;base64,…", "thumb": "data:image/jpeg;base64,…"}]}
```

| الحقل | القواعد |
|---|---|
| `name` | حرفان على الأقل (140) |
| `category` | `slug` لقسم موجود ونشط، وإلا `400` «القسم المحدد غير موجود أو غير مفعّل.» |
| `price` | عدد صحيح 0–100,000,000 (0 = حسب الطلب)؛ `stock` عدد صحيح 0–1,000,000 |
| `description` (4000)، `alt` (250؛ الافتراضي الاسم)، `badge` (60)، `sku` (80) | نصوص اختيارية |
| `images` | الصور المحتفظ بها بترتيب العرض (روابط موجودة في `/assets/` أو `/uploads/` فقط)؛ الأولى هي الرئيسية |
| `new_images` | صور جديدة base64 (`data:image/jpeg|png|webp;base64,…`) مع مصغّرة اختيارية |
| `slug` | اختياري؛ إن غاب يُبنى من `name`، وإن تكرر يُضاف `-2` و`-3`… |
| `image` / `image_data` | صيغة قديمة لصورة واحدة (ما زالت مقبولة) |

الحد 12 صورة («الحد الأقصى 12 صورة لكل منتج.»)، ويلزم صورة واحدة («أضف صورة للمنتج.»). الصور غير المستخدمة تُحذف بعد الحفظ. منتج غير موجود: `404`.

#### `DELETE /api/admin/products/<id>`

`{"ok": true}`، أو `404` «المنتج غير موجود.». بنود الطلبات القديمة تبقى بنسختها (يصبح `product_id` فارغًا).

#### `POST /api/admin/upload`

رفع صورة مسبقًا (تستخدمه لوحة الإدارة أثناء إضافة الصور). جلسة + CSRF، حتى 9 MB.

```json
{"data": "data:image/jpeg;base64,…", "thumb": "data:image/jpeg;base64,…"}
```

`201`: `{"ok": true, "url": "/uploads/8b5ecd3628a94695b19b70cc19313ea8.png", "thumb": ""}`.

الأخطاء: `400` «اختر صورة بصيغة JPG أو PNG أو WebP.»، «ملف الصورة غير صالح.»، «محتوى الملف لا يطابق نوع الصورة.» (فحص توقيع الملف)، `413` «حجم الصورة يجب ألا يتجاوز 5 ميغابايت.». الملفات المرفوعة التي لا يستخدمها أي منتج أو قسم تُكنس بعد يوم.

### الأقسام

#### `GET /api/admin/categories`

كل الأقسام مع عدد منتجاتها: `{"categories": [{"slug": "curtains", "name": "ستائر تفصيل", "sort_order": 1, "active": 1, "image": "", "description": "", "product_count": 18}]}`.

#### `POST /api/admin/categories`

`{"name": "قسم تجريبي", "description": "وصف", "image": "/uploads/….jpg"}` (أو `image_data` base64، و`slug` اختياري). `201`:

```json
{"ok": true, "slug": "قسم-تجريبي", "name": "قسم تجريبي", "image": "", "description": "وصف"}
```

الاسم حرفان على الأقل (80)، والوصف حتى 400. رابط موجود: `409` «يوجد قسم بهذا الرابط بالفعل.».

#### `POST /api/admin/categories/reorder`

`{"order": ["curtains", "roller", "blinds", "electric", "fabrics"]}` — كل الأقسام بالترتيب الجديد. `{"ok": true}`؛ قائمة غير صالحة `400`؛ لا تطابق الأقسام الحالية `409`.

#### `PUT /api/admin/categories/<slug>`

`{"name": "ستائر تفصيل", "active": true, "description": "…", "image": "/uploads/….jpg"}`؛ أو `image_data` لصورة جديدة، أو `"remove_image": true`. إن لم يُرسل `description` يبقى الوصف المحفوظ. الرابط (`slug`) لا يتغير.

```json
{"ok": true, "slug": "curtains", "name": "ستائر تفصيل", "active": true, "image": "", "description": "…"}
```

قسم غير موجود: `404`.

#### `DELETE /api/admin/categories/<slug>`

`{"ok": true}`؛ القسم فيه منتجات: `409` «انقل منتجات هذا القسم أو احذفها قبل حذف القسم.»؛ غير موجود: `404`.

### الطلبات

#### `GET /api/admin/orders?status=<الحالة>|all`

آخر 300 طلب مع بنودها، وقاموس الحالات:

```json
{"orders": [{"id": 1, "order_number": "MH-261009-00001", "customer_name": "عميل تجريبي", "phone": "0500000000",
  "city": "جدة", "address": "", "notes": "", "status": "new", "subtotal": 0, "delivery_fee": 0, "total": 0,
  "created_at": "…", "updated_at": "…", "consent_at": "…", "status_label": "جديد", "on_request": true,
  "items": [{"product_id": 16, "product_name": "ستائر ويفي تفصيل حسب الطلب — موديل 3", "sku": "FAL-WAV-003",
             "unit_price": 0, "quantity": 2}]}],
 "statuses": {"new": "جديد", "confirmed": "تم التأكيد", "preparing": "قيد التجهيز", "shipped": "تم الشحن",
              "delivered": "تم التسليم", "cancelled": "ملغي"}}
```

#### `PATCH /api/admin/orders/<id>`

`{"status": "confirmed"}` ← `{"ok": true, "order": { … }}`. الانتقال إلى `cancelled` يعيد الكميات إلى المخزون، والخروج منه يحجزها من جديد. الأخطاء: `400` «حالة الطلب غير صالحة.»، `404` «الطلب غير موجود.»، `409` «لا يمكن إعادة تفعيل الطلب؛ مخزون «…» غير كافٍ.».

#### `GET /api/admin/export/orders.csv`

كل الطلبات بملف `orders.csv` (`text/csv; charset=utf-8` مع BOM ليفتح بالعربية في Excel):

```text
رقم الطلب,الاسم,الجوال,المدينة,الحالة,المجموع,التوصيل,الإجمالي,تاريخ الإنشاء,ملاحظات
MH-261009-00001,عميل تجريبي,0500000000,جدة,تم التأكيد,0,0,0,2026-10-09T18:28:23+00:00,
```

### الرسائل

#### `GET /api/admin/messages?status=all|new|handled`

الأحدث أولًا، حتى 500، مع العدادات:

```json
{"messages": [{"id": 1, "name": "عميل تجريبي", "phone": "0500000000", "email": "", "message": "رسالة تجريبية",
  "page": "/contact", "status": "new", "status_label": "جديدة", "created_at": "…", "handled_at": "", "consent_at": "…"}],
 "counts": {"new": 1, "handled": 0, "total": 1}}
```

#### `PATCH /api/admin/messages/<id>`

`{"status": "handled"}` أو `{"status": "new"}` ← `{"ok": true, "message": { … "status": "handled", "status_label": "تمت المتابعة", "handled_at": "…" }}`. حالة غير صالحة `400`، رسالة غير موجودة `404`.

#### `DELETE /api/admin/messages/<id>`

`{"ok": true}` أو `404` «الرسالة غير موجودة.».

### الإعدادات وGoogle

#### `GET /api/admin/settings`

كل مفاتيح الإعدادات (القيم نصوص):

```json
{"settings": {"store_name": "الفخامة للأقمشة والستائر", "tagline": "…", "city": "جدة", "phone": "+966 57 648 6491",
  "whatsapp": "", "address": "…", "map_url": "https://maps.app.goo.gl/…", "delivery_fee": "0", "delivery_note": "…",
  "instagram": "", "site_ready": "0", "seo_title": "…", "seo_description": "…", "hero_title": "…", "hero_copy": "…",
  "about_title": "من نحن", "about_body": "…", "opening_hours": "", "ga4_id": "", "google_site_verification": "",
  "sample_cleanup_v1": "1", "seed_wavy_v1": "1"}}
```

#### `PUT /api/admin/settings`

تحديث جزئي: يُحفظ فقط ما يُرسل من المفاتيح المسموحة، ويعيد `{"ok": true, "settings": { … }}`.

| المفتاح | الحد / القاعدة |
|---|---|
| `store_name` 100، `tagline` 160، `city` 100، `phone` 40، `whatsapp` 40، `address` 250 | نص يُقتطع عند الحد |
| `map_url` 400، `instagram` 250 | يجب أن يبدأ بـ `http://` أو `https://`، وإلا `400` «الروابط يجب أن تبدأ بـ https:// أو http://» |
| `delivery_note` 400، `seo_title` 180، `seo_description` 320، `hero_title` 180، `hero_copy` 600 | نص |
| `about_title` 120، `about_body` 3000، `opening_hours` 300 | نص صفحة «من نحن» |
| `delivery_fee` | عدد صحيح 0–10,000,000 |
| `site_ready` | قيمة منطقية ← `"1"` أو `"0"` |
| `ga4_id` | فارغ أو `G-` ثم 4–15 حرفًا/رقمًا إنجليزيًا (تُحوَّل إلى حروف كبيرة)، وإلا `400` |
| `google_site_verification` | فارغ أو 10–120 من `A-Z a-z 0-9 _ -`؛ يُقبل لصق وسم `<meta … content="…">` كاملًا أو `google-site-verification=…` فيُستخرج الرمز، وإلا `400` |

### الأمان: كلمة المرور والتحقق بخطوتين

#### `POST /api/admin/password`

`{"current_password": "…", "new_password": "…"}` ← `{"ok": true}`، ويُنهي الجلسات الأخرى للمستخدم. الأخطاء: `400` «كلمة المرور الجديدة يجب ألا تقل عن 12 خانة.»، `403` «كلمة المرور الحالية غير صحيحة.».

#### `POST /api/admin/2fa/setup`

`{}` ← `{"secret": "<مفتاح base32>", "otpauth": "otpauth://totp/…"}`؛ المفتاح ينتظر التأكيد في الجلسة الحالية. مفعّل مسبقًا: `409`.

#### `POST /api/admin/2fa/enable`

`{"code": "123456"}` ← `{"ok": true}`. لم يبدأ الإعداد: `409` «ابدأ الإعداد أولًا.»؛ رمز خاطئ: `400` «رمز التحقق غير صحيح. تأكد من ساعة الهاتف وحاول مجددًا.».

#### `POST /api/admin/2fa/disable`

`{"password": "…", "code": "123456"}` ← `{"ok": true}`. غير مفعّل: `409`؛ كلمة مرور أو رمز خاطئ: `403`.

### النسخ الاحتياطي

#### `GET /api/admin/backups`

النسخ الموجودة على الخادم في `DATA_DIR/backups` (حتى 60، الأحدث أولًا):

```json
{"backups": [{"name": "store-20261009-030000.db", "kind": "database", "size": 118784, "modified": "2026-10-09T03:00:00+00:00"}],
 "auto_backup": true, "keep": 14}
```

`kind`: `database` أو `uploads` أو `pre-restore`.

#### `GET /api/admin/backup.zip`

جلسة فقط (قراءة). ينزّل `application/zip` باسم `fakhama-backup-<UTC>.zip` (`Content-Disposition: attachment`) يحوي `store.db` (نسخة متّسقة بواجهة النسخ الاحتياطي في SQLite، بما فيها جدول `admins`)، ومجلد `uploads/`، و`manifest.json` (التطبيق، الصيغة، الإصدار، الوقت، عدد صفوف كل جدول، عدد الصور، بصمة SHA-256 لقاعدة البيانات). العملية تُسجَّل في سجل التدقيق. الاستعادة من سطر الأوامر فقط: `python3 app.py restore <zip>`.

---

## الصفحات والملفات (ليست JSON)

| المسار | المحتوى |
|---|---|
| `GET /` و`/index.html` | الرئيسية |
| `GET /products?q=…`، `/categories`، `/category/<slug>`، `/product/<slug>`، `/about`، `/contact`، `/faq` | صفحات المتجر المولّدة في الخادم |
| `GET /privacy`، `/terms` | سياسة الخصوصية والشروط |
| أي صفحة غير موجودة | صفحة 404 بهوية المتجر (`noindex`) |
| مسار بشرطة مائلة في آخره (`/about/`) | تحويل `301` إلى المسار الرسمي |
| `GET /admin/` (و`/admin` و`/admin.html`) | لوحة الإدارة |
| `GET /assets/…`، `/icons/…`، `/uploads/…` | ملفات ثابتة فقط من هذه المجلدات (منع الخروج منها)؛ الصور المرفوعة والخطوط بتخزين مؤقت سنة (`immutable`)، والبقية يوم |
| `GET /manifest.webmanifest`، `/admin-manifest.webmanifest`، `/sw.js`، `/favicon.ico`، `/offline.html` | ملفات تطبيق الويب |
| `GET /.well-known/assetlinks.json` | فقط إن ضُبط `DIGITAL_ASSET_LINKS_FILE` |

## مثال كامل بسطر الأوامر

```bash
BASE=https://shop.example.sa          # نطاق المتجر
JAR=$(mktemp)
# 1) الدخول (اكتب كلمة المرور عند الطلب؛ لا تضعها في سجل الأوامر)
read -rs -p "Password: " PW; echo
CSRF=$(curl -s -c "$JAR" -H 'Content-Type: application/json' \
  -d "{\"username\":\"admin\",\"password\":\"$PW\"}" "$BASE/api/admin/login" | python3 -c 'import sys,json;print(json.load(sys.stdin)["csrf"])')
unset PW
# 2) قراءة الطلبات الجديدة
curl -s -b "$JAR" "$BASE/api/admin/orders?status=new"
# 3) تأكيد الطلب رقم 1 (كتابة: تحتاج X-CSRF-Token)
curl -s -b "$JAR" -X PATCH -H 'Content-Type: application/json' -H "X-CSRF-Token: $CSRF" \
  -d '{"status":"confirmed"}' "$BASE/api/admin/orders/1"
# 4) الخروج
curl -s -b "$JAR" -X POST -H 'Content-Type: application/json' -H "X-CSRF-Token: $CSRF" -d '{}' "$BASE/api/admin/logout"
rm -f "$JAR"
```
