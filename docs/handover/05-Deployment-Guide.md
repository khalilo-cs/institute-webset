# دليل النشر

يشرح هذا الدليل نشر المتجر على خادم إنتاجي **يملكه المتجر**، وربط النطاق وخدمات Google، وبناء تطبيق Android الإنتاجي، وطريقة التحديث. القاعدة في كل خطوة: **الحساب يُنشأ باسم المتجر وببريده ووسيلة دفعه، ويُضاف المطوّر مستخدمًا بصلاحيات محدودة** يمكن سحبها في أي وقت.

> **تنبيه صادق:** ملفات Docker (`Dockerfile` و`docker-compose.yml` و`deploy/Caddyfile`) لم تُشغَّل في بيئة التطوير لعدم توفر خادم Docker فيها؛ الخادم نفسه مختبَر آليًا. شغّل خطوات التحقق في نهاية القسم (أ) بعد أول نشر. شروط الخدمات المجانية وأسعار Google قد تتغير؛ راجع الصفحات الرسمية المذكورة قبل التنفيذ.

## نظرة سريعة

| القسم | الخطوة | المالك |
|---|---|---|
| أ | خادم Google Cloud ‏(e2-micro) بـ Docker وCaddy وHTTPS | حساب Google الخاص بالمتجر + فوترة المتجر |
| ب | النطاق وسجل DNS | المتجر (المطوّر جهة اتصال تقنية) |
| ج | Google Search Console وخريطة الموقع وتفعيل الجاهزية | حساب Google الخاص بالمتجر |
| د | ملف النشاط التجاري على Google ‏(Business Profile) | حساب Google الخاص بالمتجر |
| هـ | Google Analytics 4 (اختياري) | حساب Google الخاص بالمتجر |
| و | نسخة عرض على Hugging Face (للتجربة فقط) | — |
| ز | تطبيق Android الإنتاجي وGoogle Play | مفتاح توقيع المتجر + حساب Play Console باسم المتجر |
| ح | تحديث النظام (بعد نسخة احتياطية) | التقني |

## أ) الخادم على Google Cloud ‏(Compute Engine e2-micro — Always Free)

### أ-1. المشروع والفوترة باسم المتجر

1. سجّل الدخول إلى https://console.cloud.google.com بـ **حساب Google الخاص بالمتجر** (ليس حساب المطوّر).
2. أنشئ مشروعًا جديدًا (مثل `fakhama-store`).
3. اربطه بـ **حساب فوترة المتجر** (بطاقة المتجر). الطبقة المجانية تتطلب حساب فوترة مفعّلًا.
4. من **Billing ← Budgets & alerts** أنشئ ميزانية بمبلغ صغير مع تنبيهات بالبريد، لتعرف فورًا بأي تكلفة خارج الطبقة المجانية.
5. من **IAM & Admin ← IAM** أضف بريد المطوّر بأدوار محدودة تكفي لإدارة الخادم:
   - `Compute Instance Admin (v1)`
   - `IAP-secured Tunnel User` (للدخول بـ SSH عبر IAP)
   - `Service Account User`
   - ولا تمنحه دور `Owner` ولا صلاحيات الفوترة. يبقى المتجر هو **المالك (Owner)** ومسؤول الفوترة.

### أ-2. شروط الطبقة المجانية

الطبقة المجانية الدائمة لـ Compute Engine تشمل (وقت كتابة الدليل) **جهازًا واحدًا من نوع e2-micro** في إحدى المناطق **`us-west1` أو `us-central1` أو `us-east1`**، مع **30 GB من القرص القياسي** وحصة صغيرة لنقل البيانات الصادرة. ما يزيد على ذلك، وكذلك بعض البنود مثل عنوان IP الخارجي أو اللقطات (snapshots)، قد يُحتسب برسوم حسب سياسة Google الحالية. المرجع الرسمي: https://cloud.google.com/free/docs/free-cloud-features#compute

ملاحظتان:

- e2-micro جهاز صغير (ذاكرة 1 GB) يكفي متجرًا منخفض إلى متوسط الحركة بهذا النظام الخفيف.
- المناطق المجانية في الولايات المتحدة، فزمن الاستجابة من السعودية أعلى من منطقة قريبة (لدى Google منطقة في الدمام `me-central2` لكنها خارج الطبقة المجانية). استضافة بيانات العملاء خارج المملكة تستدعي مراجعة متطلبات **نظام حماية البيانات الشخصية** بشأن نقل البيانات خارج المملكة مع مختص.

### أ-3. إنشاء الجهاز والعنوان الثابت والجدار الناري

يمكن التنفيذ من واجهة Console أو من **Cloud Shell** بالأوامر التالية (عدّل المنطقة إن لزم):

```bash
gcloud config set project fakhama-store
gcloud compute addresses create fakhama-ip --region=us-central1
gcloud compute instances create fakhama-store \
  --zone=us-central1-a --machine-type=e2-micro \
  --image-family=debian-12 --image-project=debian-cloud \
  --boot-disk-size=30GB --boot-disk-type=pd-standard \
  --tags=http-server,https-server --address=fakhama-ip
gcloud compute addresses describe fakhama-ip --region=us-central1 --format='value(address)'   # عنوان IP لسجل DNS
```

**الجدار الناري: 80 و443 فقط للعامة، وSSH عبر IAP فقط:**

```bash
gcloud compute firewall-rules create allow-web --network=default --direction=INGRESS \
  --action=ALLOW --rules=tcp:80,tcp:443 --source-ranges=0.0.0.0/0 --target-tags=http-server,https-server
gcloud compute firewall-rules create allow-ssh-iap --network=default --direction=INGRESS \
  --action=ALLOW --rules=tcp:22 --source-ranges=35.235.240.0/20
gcloud compute firewall-rules delete default-allow-ssh default-allow-rdp   # قواعد الشبكة الافتراضية المفتوحة للجميع
```

- النطاق `35.235.240.0/20` هو نطاق Google لخدمة IAP؛ فلا يصل أحد إلى SSH إلا عبر حساب Google له صلاحية.
- **لا تفتح المنفذ 4173**: الخادم داخل Docker غير منشور للخارج، وCaddy وحده يستقبل 80 و443.

### أ-4. الدخول وتجهيز النظام

```bash
gcloud compute ssh fakhama-store --zone=us-central1-a --tunnel-through-iap
```

(إن طُلب تفعيل Identity-Aware Proxy API فوافق.) ثم على الخادم:

```bash
sudo apt-get update && sudo apt-get -y upgrade
sudo apt-get install -y unattended-upgrades git ca-certificates curl
sudo dpkg-reconfigure -plow unattended-upgrades          # تحديثات أمنية تلقائية
# ذاكرة تبديل 1 GB (اختياري، مفيد مع ذاكرة 1 GB)
sudo fallocate -l 1G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

### أ-5. تثبيت Docker وCompose (المستودع الرسمي)

```bash
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
# تدوير سجلات Docker (بدونه تكبر السجلات بلا حد)
echo '{"log-driver": "json-file", "log-opts": {"max-size": "10m", "max-file": "5"}}' | sudo tee /etc/docker/daemon.json
sudo systemctl restart docker && sudo systemctl enable docker
sudo usermod -aG docker "$USER"     # ثم اخرج وادخل من جديد
```

المرجع: https://docs.docker.com/engine/install/debian/

### أ-6. الشيفرة والإعداد والتشغيل الأول

1. **وجّه النطاق أولًا** (القسم ب) حتى يستطيع Caddy استخراج شهادة HTTPS.
2. انسخ الشيفرة من مستودع GitHub الخاص بالمتجر (بعد نقله، انظر «تسليم الحسابات»)، أو ارفع `source-code.zip` من الحزمة:

```bash
sudo mkdir -p /opt/fakhama && sudo chown "$USER" /opt/fakhama
git clone https://github.com/<حساب-المتجر>/institute-webset.git /opt/fakhama
cd /opt/fakhama && git checkout <المراجعة-أو-الإصدار-المعتمد>
```

   للمستودع الخاص استخدم **مفتاح نشر للقراءة فقط** (Deploy key) بدل كلمة مرور.

3. اكتب النطاق في ملف `.env` بجانب `docker-compose.yml` (يقرؤه Docker Compose فقط؛ لا يدخل صورة Docker)، ثم شغّل مع كلمة مرور المدير الأولى دون أن تُحفظ في سجل الأوامر:

```bash
cd /opt/fakhama
echo "DOMAIN=shop.example.sa" > .env                    # نطاقك الفعلي
read -rs -p "Admin password (12+): " ADMIN_PASSWORD; echo; export ADMIN_PASSWORD
docker compose up -d --build
unset ADMIN_PASSWORD
```

- `ADMIN_PASSWORD` يُستخدم **مرة واحدة فقط** عند إنشاء قاعدة البيانات أول مرة. إن تُرك فارغًا يولّد الخادم كلمة مرور عشوائية ويطبعها مرة واحدة في `docker compose logs app`.
- سلّم كلمة المرور للعميل عبر **مدير كلمات المرور**، ويغيّرها العميل من الإعدادات فور الاستلام.
- **لا تستخدم أبدًا** كلمة مرور مرحلة التصميم المحلية (ملف `.env` على جهاز المطوّر) على هذا الخادم؛ والخادم يرفض أصلًا خيار `ALLOW_WEAK_PASSWORD` حين يعمل خلف وكيل أو بعنوان عام.

ما يضبطه `Dockerfile` و`docker-compose.yml` تلقائيًا: `HOST=0.0.0.0` داخل الحاوية فقط، `DATA_DIR=/data` على وحدة التخزين `store-data`، `TRUST_PROXY=1` (كوكي Secure وHSTS)، `LOG_FORMAT=json`، `AUTO_BACKUP=1` (نسخة يومية والاحتفاظ بآخر 14)، `PUBLIC_BASE_URL=https://${DOMAIN}`، والمستخدم غير الجذري `app` داخل الحاوية، و`HEALTHCHECK` على `/api/health`.

### أ-7. التحقق بعد التشغيل

```bash
docker compose ps                                   # الخدمتان app وcaddy تعملان، وapp بحالة healthy
docker compose logs --tail=50 caddy                 # استخراج الشهادة بنجاح
curl -sS https://shop.example.sa/api/health         # {"ok": true, ... "db": true ...}
curl -sSI http://shop.example.sa/ | head -3         # تحويل دائم إلى https://
curl -sSI https://shop.example.sa/ | grep -i -E 'strict-transport|content-security'
```

ثم افتح `https://shop.example.sa/admin/`، وسجّل الدخول، و**غيّر كلمة المرور وفعّل التحقق بخطوتين**، وجرّب طلبًا ورسالة تجريبيين ثم احذف الرسالة وألغِ الطلب.

### أ-8. البيانات والنسخ الاحتياطي

- **كل البيانات** (قاعدة البيانات والصور والنسخ اليومية) في وحدة التخزين `store-data` (`/data` داخل الحاوية). **لا تشغّل أبدًا `docker compose down -v`**؛ الخيار `-v` يحذف وحدات التخزين وما فيها.
- **نسخ يومية تلقائية على الخادم** (`AUTO_BACKUP=1`) في `/data/backups`، وتظهر في لوحة الإدارة ← «النسخ الاحتياطي». هذه لا تحمي من تعطل الخادم أو حذفه.
- **نسخة أسبوعية خارج الخادم (إلزامية):** صاحب المتجر ينزّل من لوحة الإدارة ← «إعدادات المتجر» ← «النسخ الاحتياطي» ← **«تنزيل نسخة احتياطية الآن (ZIP)»**، ويرفع الملف إلى مجلد خاص غير مشارك في **Google Drive الخاص بالمتجر**، ويحتفظ بعدة أسابيع.
- نسخة يدوية على الخادم أو نسخ مجلد النسخ إلى خارج الحاوية:

```bash
docker compose exec app python3 -I app.py backup
docker compose cp app:/data/backups ./backups-copy
```

**الاستعادة من ملف ZIP** (يتحقق الأمر من الأرشيف كاملًا ويحفظ الحالة الحالية قبل الاستبدال):

```bash
cd /opt/fakhama
chmod 644 fakhama-backup-XXXXXXXX-XXXXXX.zip         # ليقرأه مستخدم الحاوية
docker compose stop app
docker compose run --rm --no-deps -v "$PWD/fakhama-backup-XXXXXXXX-XXXXXX.zip:/tmp/restore.zip:ro" \
  app python3 -I app.py restore /tmp/restore.zip
docker compose start app
rm fakhama-backup-XXXXXXXX-XXXXXX.zip                 # الملف فيه بيانات العملاء
```

### أ-9. التشغيل دون Docker (بديل)

شغّل `python3 app.py` كخدمة systemd على `127.0.0.1` بمستخدم غير جذري، مع `DATA_DIR` على قرص دائم و`TRUST_PROXY=1` و`PUBLIC_BASE_URL=https://<النطاق>` و`AUTO_BACKUP=1` و`LOG_FORMAT=json`، خلف Caddy أو nginx بشهادة HTTPS. الخادم يتطلب Python 3.10 فأحدث ولا يحتاج أي حزمة.

## ب) النطاق (Domain)

1. **سجّل النطاق باسم المتجر أو صاحبه** لدى مسجّل نطاقات، بحساب ببريد المتجر، مع:
   - التجديد التلقائي، وقفل النقل (Registrar lock)، والتحقق بخطوتين على حساب المسجّل.
   - المطوّر **جهة اتصال تقنية** أو مستخدم مفوَّض بصلاحيات DNS فقط، لا مالكًا.
   - نطاقات `.sa` لها متطلبات تسجيل خاصة لدى الجهات المعتمدة؛ اتبع متطلبات المسجّل.
2. **سجل DNS:** أضف سجل **A** يشير إلى عنوان IP الثابت من (أ-3):

| النوع | الاسم | القيمة |
|---|---|---|
| A | `@` (للنطاق الرئيسي) أو `shop` (لنطاق فرعي مثل shop.example.sa) | عنوان IP الثابت |

3. تحقق من الانتشار: `dig +short shop.example.sa` يعيد العنوان نفسه.
4. إن كان DNS لدى خدمة وكيل (مثل Cloudflare) فاجعل السجل «DNS only» حتى يستخرج Caddy الشهادة مباشرة.
5. **النطاق www:** ملف `deploy/Caddyfile` يخدم النطاق المحدد في `DOMAIN` فقط. لإضافة `www` بتحويل إلى النطاق الرئيسي: أضف سجل A لـ `www` وأضف إلى `deploy/Caddyfile` الكتلة التالية ثم `docker compose restart caddy`:

```text
www.{$DOMAIN} {
	redir https://{$DOMAIN}{uri} permanent
}
```

6. البريد باسم النطاق (اختياري، مثل Google Workspace): سجلات MX وSPF الخاصة بمزوّد البريد لا تتعارض مع سجل A.

## ج) Google Search Console

1. بحساب Google **الخاص بالمتجر** افتح https://search.google.com/search-console وأضف خاصية:
   - **خاصية نطاق (Domain)** — موصى بها: أضف سجل **TXT** الذي تعطيه Google (`google-site-verification=…`) في DNS. تغطي كل النطاقات الفرعية وHTTP وHTTPS.
   - أو **بادئة عنوان URL**: اختر طريقة «علامة HTML»، والصق الوسم أو قيمة `content` في لوحة الإدارة ← «جوجل والتحليلات» ← **«رمز التحقق من Google Search Console»** ← «حفظ إعدادات جوجل»، ثم اضغط «تحقق» في Search Console.
   - اترك السجل أو الوسم في مكانه بعد التحقق.
2. في لوحة الإدارة فعّل **«بيانات المتجر حقيقية وجاهزة للنشر والفهرسة في Google»** (بعد مراجعة البيانات والسياسات). قبل ذلك تعطي `/sitemap.xml` الرمز 404 ويمنع `robots.txt` الزواحف.
3. في Search Console ← **Sitemaps** أرسل `sitemap.xml`.
4. من **فحص عنوان URL** اطلب فهرسة الصفحة الرئيسية.
5. من **الإعدادات ← المستخدمون والأذونات** أضف المطوّر بصلاحية **«مستخدم كامل» (Full user)**؛ يبقى المتجر **المالك**.

## د) الملف التجاري على Google (Business Profile)

1. بحساب Google الخاص بالمتجر افتح https://business.google.com وابحث عن النشاط (رابط الخرائط الحالي في الإعدادات: `https://maps.app.goo.gl/kn2kxuPujQT9raLG7`).
2. إن لم يكن الملف موثّقًا باسم المتجر فاطلب ملكيته أو أنشئه، وأكمل التحقق بالطريقة التي تحددها Google.
3. طابق **الاسم والعنوان والهاتف** حرفيًا مع إعدادات المتجر في لوحة الإدارة، وأضف **رابط الموقع** `https://<النطاق>`، والفئة، والصور، وأوقات العمل (وأضف الأوقات نفسها في «صفحة من نحن» ← «أوقات العمل»).
4. أضف المطوّر **مديرًا (Manager)** إن احتاج، ويبقى المتجر **المالك الأساسي**.
5. لا يستخدم الموقع أي مفتاح Google Maps API؛ رابط الخرائط رابط عادي.

## هـ) Google Analytics 4 (اختياري)

1. بحساب Google الخاص بالمتجر: https://analytics.google.com ← إنشاء حساب باسم المتجر ← خاصية (المنطقة الزمنية: السعودية، العملة: ريال سعودي) ← مصدر بيانات **ويب** بعنوان الموقع.
2. انسخ **معرّف القياس** `G-XXXXXXXXXX` والصقه في لوحة الإدارة ← «جوجل والتحليلات» ← **«معرّف القياس في Google Analytics 4»** ← «حفظ إعدادات جوجل».
3. النتيجة: يظهر للزوار شريط موافقة («قبول»/«رفض»)، ولا يُحمَّل Analytics إلا بعد «قبول»، وتُضاف فقرة Google Analytics إلى سياسة الخصوصية تلقائيًا، وتتسع سياسة CSP لصفحات المتجر فقط.
4. في Analytics ← الإدارة ← جمع البيانات: راجع **مدة الاحتفاظ بالبيانات** بما يتوافق مع سياسة الخصوصية.
5. امنح المطوّر صلاحية **«محرّر» (Editor)**؛ يبقى المتجر المالك.
6. لإيقاف Analytics: امسح الحقل واحفظ؛ يختفي الشريط وتعود CSP كما كانت.

## و) نسخة عرض على Hugging Face Spaces (للتجربة فقط)

مجلد `deploy/huggingface/` فيه `Dockerfile` و`README.md` جاهزان لمساحة Docker:

1. أنشئ Space من نوع **Docker** (يفضَّل بحساب المتجر، واجعلها خاصة إن لم تكن للعرض العام).
2. ضع في جذر مستودع المساحة: `Dockerfile` و`README.md` من `deploy/huggingface/`، ومعهما من جذر الشيفرة: `app.py` و`seo.py` و`pages.py` و`legal.py` و`totp.py` و`index.html` و`admin.html` و`offline.html` و`sw.js` و`manifest.webmanifest` و`admin-manifest.webmanifest` ومجلدا `assets/` و`icons/`.
3. من **Settings ← Variables and secrets** أضف سرًّا باسم `ADMIN_PASSWORD` (12 خانة فأكثر).

> **قرص المساحة مؤقت:** كل ما يُضاف بعد التشغيل (الطلبات والرسائل والصور وتغيير كلمة المرور) **يُفقد** عند إعادة تشغيل المساحة أو إعادة بنائها، وقد تتوقف المساحة المجانية بعد فترة خمول. **لا تستخدمها لطلبات حقيقية**، ولا تفعّل فيها «جاهزة للنشر»، ولا تنشر رابطها كموقع رسمي للمتجر.

## ز) تطبيق Android الإنتاجي

### ز-1. المتطلبات

- نطاق دائم يعمل بـ HTTPS (الأقسام أ و ب).
- JDK 17+ وAndroid SDK (المنصة `android-37.0` وأدوات البناء `36.0.0`)، وملف `android/local.properties` بالسطر `sdk.dir=/مسار/الـSDK` (خارج Git).

### ز-2. مفتاح التوقيع ملك المتجر

ينشئه المتجر (أو المطوّر بحضوره) على جهاز المتجر، ويحتفظ المتجر بالملف وكلمتي المرور:

```bash
keytool -genkeypair -v -keystore alfakhamah-release.jks -alias alfakhamah \
        -keyalg RSA -keysize 4096 -validity 10000
```

- احفظ ملف `.jks` وكلمتي المرور في **مدير كلمات المرور الخاص بالمتجر** مع نسخة احتياطية منفصلة. **لا يُرفع إلى Git** (الملفات `*.jks` و`*.keystore` مستثناة في `.gitignore`).
- كل تحديث للتطبيق يجب أن يُوقَّع بالمفتاح نفسه؛ فقدانه يمنع تحديث النسخة الموزعة مباشرة (APK).

### ز-3. البناء

```bash
export SERVER_ORIGIN=https://shop.example.sa              # النطاق الدائم، بلا مسار
export ANDROID_KEYSTORE_FILE=/مسار/alfakhamah-release.jks
read -rs -p "Keystore password: " ANDROID_KEYSTORE_PASSWORD; echo; export ANDROID_KEYSTORE_PASSWORD
export ANDROID_KEY_ALIAS=alfakhamah
read -rs -p "Key password: " ANDROID_KEY_PASSWORD; echo; export ANDROID_KEY_PASSWORD
./android/scripts/build-release.sh
```

- يشغّل السكربت اختبارات الوحدة ثم يبني، ويضع الناتج في `artifacts/release/`: **`alfakhamah.apk`** (للتوزيع المباشر) و**`alfakhamah.aab`** (للرفع إلى Google Play) مع `SHA256SUMS.txt`، ويطبع بصمة شهادة التوقيع.
- **حارس الإصدار** يرفض البناء إن لم يكن `SERVER_ORIGIN` نطاقًا دائمًا بـ `https://` (يرفض `http://` و`localhost` وعناوين IP والنطاقات المؤقتة والوهمية) أو إن غابت بيانات التوقيع.
- **جرّب APK على هاتف حقيقي** قبل النشر: التصفح، الطلب، دخول الإدارة، رفع صورة، تنزيل CSV، روابط الخرائط والهاتف.

### ز-4. Google Play Console باسم المتجر

1. أنشئ حساب مطوّر في https://play.google.com/console **باسم المتجر وبحسابه** (رسوم تسجيل لمرة واحدة). حساب **المؤسسة** يتطلب رقم D-U-N-S، والحسابات **الشخصية** الجديدة تفرض Google عليها اختبارًا مغلقًا بعدد من المختبرين ومدة محددة قبل النشر العام؛ راجع الشروط الحالية عند التسجيل.
2. أكّد معرّف التطبيق `com.alfakhamah.store` قبل أول رفع؛ **لا يمكن تغييره بعد النشر**.
3. ارفع `alfakhamah.aab`. تستخدم Google Play ميزة **Play App Signing**: تحتفظ Google بمفتاح توقيع التطبيق، ويصبح مفتاحك **مفتاح الرفع** (Upload key) فاحتفظ به.
4. أكمل صفحة المتجر ومحتوى التطبيق: رابط **سياسة الخصوصية** `https://<النطاق>/privacy`، ونموذج **أمان البيانات** (Data safety): مع الطلب يُجمع الاسم والجوال والمدينة والعنوان والملاحظة؛ ومع رسالة التواصل الاسم والجوال والبريد الاختياري والرسالة؛ لا دفع إلكتروني ولا إعلانات. الأيقونة 512 بكسل جاهزة في `android/store-assets/`.
5. التطبيقات المبنية على WebView قد تخضع لسياسة «الحد الأدنى من الوظائف»؛ القبول قرار Google.
6. أضف المطوّر من **المستخدمون والأذونات** بصلاحيات الإصدار فقط؛ يبقى المتجر **مالك الحساب**.

## ح) إجراء التحديث

**قبل أي تحديث: نسخة احتياطية أولًا.**

```bash
cd /opt/fakhama
# 1) نسختان: واحدة على الخادم، وأخرى ينزّلها المالك من لوحة الإدارة إلى Google Drive
docker compose exec app python3 -I app.py backup
# 2) جلب الإصدار الجديد ومراجعة سجل التغييرات
git fetch --tags && git log --oneline HEAD..origin/main
git checkout <الإصدار-الجديد>
# 3) إعادة البناء والتشغيل (تُرقّى قاعدة البيانات تلقائيًا عند الإقلاع)
docker compose up -d --build
# 4) التحقق
docker compose ps && curl -sS https://shop.example.sa/api/health
docker compose logs --tail=100 app
```

- إعادة التشغيل تُنهي جلسة المدير؛ سجّل الدخول مجددًا وجرّب الصفحات الرئيسية واللوحة.
- **التراجع:** `git checkout <الإصدار-السابق> && docker compose up -d --build`. وإن لزم إرجاع البيانات أيضًا فاستعد نسخة ما قبل التحديث (أ-8).
- **تحديث المكوّنات دوريًا:** `docker compose build --pull && docker compose pull caddy && docker compose up -d` لتحديث صورة Python الأساسية وCaddy؛ وتحديثات النظام تتم تلقائيًا (`unattended-upgrades`)، والحاويات تعود للعمل بعد إعادة تشغيل الجهاز (`restart: unless-stopped`).

## قائمة ما قبل الإطلاق

- [ ] الخادم يعمل بـ HTTPS، و`/api/health` يعيد `ok`، وHTTP يتحول إلى HTTPS.
- [ ] الجدار الناري: 80 و443 للعامة فقط، وSSH عبر IAP.
- [ ] كلمة مرور المدير غُيّرت بعد الاستلام، والتحقق بخطوتين مفعّل.
- [ ] بيانات المتجر وصفحة «من نحن» صحيحة، وواتساب فارغ حتى يُؤكَّد الرقم.
- [ ] سياسة الخصوصية والشروط روجعتا قانونيًا.
- [ ] خيار «جاهزة للنشر والفهرسة» مفعّل، وخريطة الموقع مرسلة إلى Search Console.
- [ ] Business Profile يطابق بيانات الموقع ويشير إليه.
- [ ] أول نسخة احتياطية محفوظة في Google Drive الخاص بالمتجر، وتذكير أسبوعي مضبوط.
- [ ] تنبيه الميزانية في Google Cloud مفعّل.
