// Browser end-to-end check of the storefront and admin UI (Chromium via Playwright, phone-sized viewport).
//   NODE_PATH=$(npm root -g) node tests/e2e_ui.cjs [screenshot-dir]
// Starts its own server on a free port with a throw-away DATA_DIR and a random admin password.
const { chromium } = require('playwright');
const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const net = require('net');
const path = require('path');
const crypto = require('crypto');
const assert = require('assert');

const ROOT = path.resolve(__dirname, '..');
const SHOTS = process.argv[2] || fs.mkdtempSync(path.join(os.tmpdir(), 'fakhama-shots-'));
fs.mkdirSync(SHOTS, { recursive: true });

const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==', 'base64');

function freePort() {
  return new Promise(resolve => { const s = net.createServer(); s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => resolve(p)); }); });
}
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const port = await freePort();
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'fakhama-e2e-'));
  const password = 'E' + crypto.randomBytes(14).toString('base64url');
  const server = spawn('python3', ['-I', path.join(ROOT, 'app.py')], {
    cwd: dataDir, env: { ...process.env, PORT: String(port), HOST: '127.0.0.1', DATA_DIR: dataDir, ADMIN_PASSWORD: password, ENV_FILE: 'off' }, stdio: ['ignore', 'pipe', 'pipe'],
  });
  let serverLog = '';
  server.stderr.on('data', d => { serverLog += d; });
  for (let i = 0; i < 100; i++) { try { await fetch(`http://127.0.0.1:${port}/api/health`); break; } catch { await sleep(100); } }
  const base = `http://127.0.0.1:${port}`;

  const browser = await chromium.launch();
  const results = [];
  const check = (name, fn) => async () => { try { await fn(); results.push(['PASS', name]); } catch (e) { results.push(['FAIL', name + ' — ' + e.message.split('\n').slice(0, 3).join(' | ')]); } };
  const errors = [];
  let newPassword = '';

  // --- helpers for the server-rendered pages
  // Browser errors on a page are failures, except a 4xx on a path whose 404 is the point of the check.
  const expectedUrl = (url, paths) => { try { const u = new URL(url); return u.origin === base && paths.includes(u.pathname); } catch { return false; } };
  const watch = (pg, label, expected = []) => {
    pg.on('console', m => { if (m.type() === 'error' && !expectedUrl(m.location().url, expected)) errors.push(`${label} console: ${m.text()}`); });
    pg.on('pageerror', e => errors.push(`${label} pageerror: ${e.message}`));
    pg.on('response', r => { if (r.status() >= 400 && !expectedUrl(r.url(), expected)) errors.push(`${label} http ${r.status()}: ${r.url()}`); });
  };
  // Click a link (or press a key in a form) and return the HTTP response of the page it leads to.
  const follow = async (pg, action) => { const [resp] = await Promise.all([pg.waitForNavigation(), action()]); return resp; };
  const catalogReady = pg => pg.waitForFunction(() => typeof catalogState !== 'undefined' && catalogState === 'ready');
  // "page" on the list page itself, "true" for its section (a product or category page inside it)
  const currentNav = pg => pg.$$eval('#mainNav a[aria-current]:not([aria-current="false"])', as => as.map(a => a.getAttribute('href')));
  const oneH1 = async (pg, text) => {
    const h1s = await pg.$$eval('h1', els => els.map(e => e.textContent.trim()));
    assert.strictEqual(h1s.length, 1, 'exactly one h1: ' + JSON.stringify(h1s));
    if (text !== undefined) assert.strictEqual(h1s[0], text);
  };
  // Wait for a toast, then clear it so a later wait cannot be satisfied by this one's text.
  const toastSays = async (pg, text) => {
    await pg.waitForFunction(t => document.querySelector('#toast').textContent.includes(t), text);
    await pg.evaluate(() => { document.querySelector('#toast').textContent = ''; });
  };
  // Screenshots of a settled page: wait for the finite entrance animations (the looping shimmer never ends).
  const settled = pg => pg.evaluate(() => Promise.all(document.getAnimations().filter(a => Number.isFinite(a.effect?.getComputedTiming().endTime)).map(a => a.finished.catch(() => {}))));
  const apiProducts = async () => (await (await fetch(base + '/api/products')).json()).products;
  // the storefront search haystack (index.html renderProducts / pages.search_matches)
  const searchCount = (products, term) => products.filter(p => `${p.name} ${p.category_name} ${p.description} ${p.sku || ''}`.toLowerCase().includes(term.toLowerCase())).length;
  const pathAndQ = url => { const u = new URL(url); return [u.pathname, u.searchParams.get('q')]; };

  try {
    // ------------------------------------------------------------ customer storefront (phone)
    const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, locale: 'ar-SA', isMobile: true, hasTouch: true });
    const page = await phone.newPage();
    page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
    page.on('pageerror', e => errors.push('pageerror: ' + e.message));
    page.on('requestfailed', r => errors.push('requestfailed: ' + r.url()));
    page.on('response', r => { if (r.status() >= 400) errors.push(`http ${r.status()}: ${r.url()}`); });

    await page.goto(base + '/');
    await page.waitForSelector('.product-card');
    await check('storefront lists the 18 owner photos (no invented sample products)', async () => assert.strictEqual(await page.locator('.product-card').count(), 18))();
    await check('only categories with products are shown (cards + filter chips use real photos)', async () => {
      assert.strictEqual(await page.locator('#categoryGrid .category-card').count(), 1);
      assert.strictEqual(await page.locator('#filterList .filter-chip').count(), 2);
      assert.ok((await page.getAttribute('#categoryGrid .category-card img', 'src')).startsWith('/assets/wavy-'));
    })();
    await check('owner photos show "price on request", not 0 SAR', async () => {
      const first = await page.locator('.product-card').first().textContent();
      assert.ok(first.includes('ويفي') && first.includes('السعر حسب الطلب') && !first.includes('٠'), first);
    })();
    await check('all product images decode', async () => {
      // images are lazy-loaded: force them, then require every one to decode
      const results = await page.$$eval('img.product-image', imgs => Promise.all(imgs.map(i => { i.loading = 'eager'; return i.decode().then(() => i.naturalWidth > 0, () => false); })));
      assert.strictEqual(results.filter(ok => !ok).length, 0);
    })();
    await check('manifest + SW registered and active', async () => {
      const href = await page.getAttribute('link[rel=manifest]', 'href');
      assert.strictEqual(href, '/manifest.webmanifest');
      const scope = await page.evaluate(async () => (await navigator.serviceWorker.ready).scope);
      assert.strictEqual(scope, base + '/');
    })();
    await page.screenshot({ path: path.join(SHOTS, '01-store-mobile.png') });

    await check('search filters products', async () => {
      await page.click('#searchToggle'); // the search box is collapsed on phones
      await page.fill('#searchInput', 'FAL-WAV-005');
      await page.waitForTimeout(150);
      const n = await page.locator('.product-card').count();
      assert.strictEqual(n, 1, 'cards=' + n);
      await page.fill('#searchInput', '');
    })();

    await check('add to cart and place an order through the UI', async () => {
      await page.locator('.quick-add').first().click({ force: true });
      await page.waitForSelector('#productModal.open');
      await page.click('[data-modal-add]');
      await page.click('#cartOpen');
      await page.waitForSelector('#cartDrawer.open');
      await page.screenshot({ path: path.join(SHOTS, '02-cart-mobile.png') });
      await page.click('#checkoutBtn');
      await page.waitForSelector('#orderModal.open');
      await page.fill('#customerName', 'عميل اختبار الواجهة');
      await page.fill('#customerPhone', '0551234567');
      await page.fill('#customerCity', 'جدة');
      await page.check('#orderForm input[name=consent]');
      await page.screenshot({ path: path.join(SHOTS, '03-order-form-mobile.png') });
      await page.click('#orderForm button[type=submit]');
      await page.waitForFunction(() => /تم تسجيل طلبك MH-\d{6}-\d{5} بنجاح/.test(document.querySelector('#toast').textContent));
      assert.strictEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('fakhama-cart-v1') || '[]').length), 0, 'cart cleared');
    })();

    // ------------------------------------------------------------ admin (phone)
    const admin = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, locale: 'ar-SA', isMobile: true, hasTouch: true });
    const ap = await admin.newPage();
    ap.on('pageerror', e => errors.push('admin pageerror: ' + e.message));
    ap.on('response', r => { if (r.status() >= 400 && !r.url().includes('/api/admin/me')) errors.push(`admin http ${r.status()}: ${r.url()}`); });
    await ap.goto(base + '/admin/');
    await ap.waitForSelector('#loginForm');
    await ap.screenshot({ path: path.join(SHOTS, '05-admin-login-mobile.png') });
    await check('admin login rejects a wrong password', async () => {
      await ap.fill('#loginUser', 'admin'); await ap.fill('#loginPass', 'wrong-password-123');
      await ap.click('#loginForm button[type=submit]');
      await ap.waitForFunction(() => document.querySelector('#loginError').textContent.length > 0);
    })();
    await check('admin login works and dashboard shows the new order', async () => {
      await ap.fill('#loginPass', password);
      await ap.click('#loginForm button[type=submit]');
      await ap.waitForSelector('.stats-grid');
      assert.ok((await ap.textContent('#pageContent')).includes('MH-'));
      await ap.screenshot({ path: path.join(SHOTS, '06-admin-dashboard-mobile.png') });
    })();
    await check('admin product thumbnails load (relative paths used to 404 under /admin/)', async () => {
      await ap.evaluate(() => navigate('products'));
      await ap.waitForSelector('#productsBody .thumb');
      await ap.waitForTimeout(500);
      const results = await ap.$$eval('#productsBody img.thumb', imgs => Promise.all(imgs.map(i => { i.loading = 'eager'; return i.decode().then(() => i.naturalWidth > 0, () => false); })));
      assert.strictEqual(results.filter(ok => !ok).length, 0);
      assert.ok((await ap.textContent('#productsBody')).includes('حسب الطلب'), 'admin shows price on request');
      await ap.screenshot({ path: path.join(SHOTS, '07-admin-products-mobile.png') });
    })();
    const png = path.join(dataDir, 'upload-test.png');
    fs.writeFileSync(png, PNG);
    const png2 = path.join(dataDir, 'upload-test-2.png');
    fs.writeFileSync(png2, PNG);
    await check('admin adds a product with TWO uploaded images, reorders them; the storefront shows the gallery', async () => {
      await ap.click('#addProduct');
      await ap.waitForSelector('#productForm');
      await ap.setInputFiles('#productImages', [png, png2]);
      await ap.waitForFunction(() => document.querySelectorAll('#gmList .gm-item').length === 2, null, { timeout: 15000 });
      await ap.click('#gmList .gm-item:nth-child(2) [data-gm=first]'); // make the second photo the primary one
      await ap.fill('#pName', 'ستارة اختبار الرفع');
      await ap.selectOption('#pCategory', 'roller');
      await ap.fill('#pPrice', '321');
      await ap.fill('#pStock', '7');
      await ap.fill('#pDescription', 'وصف تجريبي للمنتج');
      await ap.screenshot({ path: path.join(SHOTS, '08-admin-product-form-mobile.png') });
      await ap.click('#productForm button[type=submit]');
      await ap.waitForFunction(() => document.querySelector('#productsBody').textContent.includes('ستارة اختبار الرفع'));
      const res = await (await fetch(base + '/api/products')).json();
      const made = res.products.find(p => p.name === 'ستارة اختبار الرفع');
      assert.ok(made && made.images.length === 2, JSON.stringify(made));
      assert.ok(/^\/uploads\/[0-9a-f]{32}\.jpg$/.test(made.image), 'client re-encodes to JPEG: ' + made.image);
      assert.ok(/^\/uploads\/[0-9a-f]{32}\.jpg$/.test(made.thumb));
      assert.strictEqual((await fetch(base + made.image)).status, 200);
      // the product has its own shareable page with server-rendered meta
      const html = await (await fetch(base + '/product/' + encodeURIComponent(made.slug))).text();
      assert.ok(html.includes('og:title') && html.includes('ستارة اختبار الرفع') && html.includes('"Product"'));
    })();
    await check('admin bar appears on the storefront for a logged-in admin; product modal has gallery, share and edit', async () => {
      const sp = await admin.newPage();
      await sp.goto(base + '/');
      await sp.waitForSelector('.product-card');
      await sp.waitForSelector('#adminBar:not([hidden])');
      assert.ok((await sp.getAttribute('#adminBar a', 'href')).startsWith('/admin/#products/new'));
      await sp.fill('#searchInput', 'ستارة اختبار الرفع').catch(async () => { await sp.click('#searchToggle'); await sp.fill('#searchInput', 'ستارة اختبار الرفع'); });
      await sp.locator('.quick-add').first().click({ force: true });
      await sp.waitForSelector('#productModal.open');
      assert.strictEqual(await sp.locator('.gthumb').count(), 2);
      assert.ok(sp.url().includes('/product/'), 'URL follows the open product: ' + sp.url());
      assert.ok((await sp.title()).includes('ستارة اختبار الرفع'));
      await sp.click('.gthumb:nth-child(2)');
      assert.ok((await sp.getAttribute('.gthumb:nth-child(2)', 'aria-pressed')) === 'true');
      assert.ok(await sp.locator('text=تعديل المنتج').count() === 1, 'admin edit link');
      assert.ok((await sp.getAttribute('a:has-text("مشاركة عبر واتساب")', 'href')).startsWith('https://wa.me/?text='));
      await sp.keyboard.press('Escape');
      await sp.waitForFunction(() => !document.querySelector('#productModal.open'));
      await sp.waitForFunction(() => location.pathname === '/');
      // a product link is now a real server-rendered page (it no longer re-opens the quick view on the home page)
      await sp.goto(base + '/product/wavy-03');
      await sp.waitForSelector('#productTitle');
      assert.ok((await sp.textContent('#productTitle')).includes('موديل 3'));
      await sp.waitForLoadState('networkidle'); await sp.waitForTimeout(300);
      assert.strictEqual(await sp.locator('#productModal.open').count(), 0, 'quick view stays closed on the product page');
      await sp.close();
    })();

    // ------------------------------------------------------------ server-rendered pages (a fresh visitor on desktop and phone)
    const NOT_FOUND_PATHS = ['/no-such-page', '/product/no-such-product'];
    const visitor = await browser.newContext({ viewport: { width: 1366, height: 800 }, locale: 'ar-SA' });
    await visitor.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: base });
    const vp = await visitor.newPage();
    watch(vp, 'pages (desktop)', NOT_FOUND_PATHS);
    const mobile = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, locale: 'ar-SA', isMobile: true, hasTouch: true });
    const mp = await mobile.newPage();
    watch(mp, 'pages (phone)', NOT_FOUND_PATHS);
    const GALLERY_NAME = 'ستارة معرض صفحة المنتج';
    let galleryProduct = null;

    await check('header navigation reaches every page: HTTP 200, exactly one h1, aria-current on its own menu link (desktop + phone menu)', async () => {
      await vp.goto(base + '/');
      await vp.waitForSelector('.product-card');
      assert.deepStrictEqual(await currentNav(vp), ['/'], 'home');
      for (const [href, name] of [['/products', 'products'], ['/categories', 'categories'], ['/about', 'about'], ['/faq', 'faq'], ['/contact', 'contact'], ['/', 'home']]) {
        const resp = await follow(vp, () => vp.click(`#mainNav a[href="${href}"]`));
        assert.strictEqual(resp && resp.status(), 200, href + ' status');
        assert.strictEqual(new URL(vp.url()).pathname, href);
        assert.strictEqual(await vp.getAttribute('body', 'data-page'), name, href + ' body[data-page]');
        await oneH1(vp);
        assert.deepStrictEqual(await currentNav(vp), [href], href + ' aria-current');
      }
      // phone: the menu is a drawer behind #menuToggle
      await mp.goto(base + '/');
      await mp.click('#menuToggle');
      await mp.waitForSelector('#mainNav.open');
      assert.strictEqual(await mp.getAttribute('#menuToggle', 'aria-expanded'), 'true');
      const resp = await follow(mp, () => mp.click('#mainNav a[href="/about"]'));
      assert.strictEqual(resp && resp.status(), 200);
      await oneH1(mp);
      assert.deepStrictEqual(await currentNav(mp), ['/about']);
      assert.strictEqual(await mp.locator('#mainNav.open').count(), 0, 'the menu starts closed on the new page');
    })();

    await check('/products?q= filters the server-rendered list (no JavaScript) and the live list; typing keeps the URL in sync', async () => {
      const all = await apiProducts();
      const nojs = await browser.newContext({ javaScriptEnabled: false, locale: 'ar-SA' });
      const np = await nojs.newPage();
      const resp = await np.goto(base + '/products?q=FAL-WAV-005');
      assert.strictEqual(resp.status(), 200);
      assert.strictEqual(await np.locator('#productGrid .product-card').count(), 1, 'server-rendered result');
      assert.ok((await np.textContent('#productGrid')).includes('FAL-WAV-005') || (await np.textContent('#productGrid')).includes('موديل 5'));
      assert.strictEqual(await np.inputValue('#searchInput'), 'FAL-WAV-005', 'the header search shows the term');
      assert.ok((await np.getAttribute('meta[name=robots]', 'content')).includes('noindex'), 'a search result page is not indexed');
      await nojs.close();
      await vp.goto(base + '/products?q=FAL-WAV-005');
      await catalogReady(vp);
      assert.strictEqual(await vp.locator('#productGrid .product-card').count(), 1);
      assert.strictEqual(await vp.inputValue('#searchInput'), 'FAL-WAV-005');
      assert.deepStrictEqual(await currentNav(vp), ['/products']);
      await vp.fill('#searchInput', 'FAL-WAV-01');
      await vp.waitForFunction(n => document.querySelectorAll('#productGrid .product-card').length === n, searchCount(all, 'FAL-WAV-01'));
      assert.deepStrictEqual(pathAndQ(vp.url()), ['/products', 'FAL-WAV-01'], 'the address follows the search');
      await vp.fill('#searchInput', 'لا-يوجد-منتج-بهذا-الاسم');
      await vp.waitForSelector('#productGrid .no-results');
      assert.strictEqual(await vp.locator('#productGrid .product-card').count(), 0);
      await vp.fill('#searchInput', '');
      await vp.waitForFunction(n => document.querySelectorAll('#productGrid .product-card').length === n, all.length);
      assert.strictEqual(new URL(vp.url()).search, '', 'clearing the search clears ?q=');
      await settled(vp); await vp.screenshot({ path: path.join(SHOTS, '20-products-desktop.png') });
    })();

    await check('product page: a two-photo product added in the admin — gallery thumbs switch (click + keyboard), add to cart counts, copy link', async () => {
      await ap.evaluate(() => navigate('products'));
      await ap.waitForSelector('#addProduct');
      await ap.click('#addProduct');
      await ap.waitForSelector('#productForm');
      await ap.setInputFiles('#productImages', [png, png2]);
      await ap.waitForFunction(() => document.querySelectorAll('#gmList .gm-item').length === 2, null, { timeout: 15000 });
      await ap.fill('#pName', GALLERY_NAME);
      await ap.selectOption('#pCategory', 'blinds');
      await ap.fill('#pPrice', '450');
      await ap.fill('#pStock', '4');
      await ap.fill('#pDescription', 'وصف تجريبي لصفحة المنتج');
      await ap.click('#productForm button[type=submit]');
      await ap.waitForFunction(n => document.querySelector('#productsBody').textContent.includes(n), GALLERY_NAME);
      galleryProduct = (await apiProducts()).find(p => p.name === GALLERY_NAME);
      assert.ok(galleryProduct && galleryProduct.images.length === 2, JSON.stringify(galleryProduct));
      const url = base + '/product/' + encodeURIComponent(galleryProduct.slug);
      const resp = await vp.goto(url);
      assert.strictEqual(resp.status(), 200);
      assert.strictEqual(await vp.getAttribute('body', 'data-page'), 'product');
      await oneH1(vp, GALLERY_NAME);
      assert.deepStrictEqual(await currentNav(vp), ['/products']);
      await catalogReady(vp);
      // gallery
      const thumbs = vp.locator('.product-gallery-thumbs [data-page-gindex]');
      assert.strictEqual(await thumbs.count(), 2);
      const [first, second] = galleryProduct.images.map(i => i.url);
      assert.notStrictEqual(first, second);
      const mainSrc = src => vp.waitForFunction(s => document.querySelector('#pageGalleryMain').getAttribute('src') === s, src);
      assert.strictEqual(await vp.getAttribute('#pageGalleryMain', 'src'), first);
      assert.strictEqual(await thumbs.nth(0).getAttribute('aria-pressed'), 'true');
      await thumbs.nth(1).click();
      await mainSrc(second);
      assert.deepStrictEqual([await thumbs.nth(0).getAttribute('aria-pressed'), await thumbs.nth(1).getAttribute('aria-pressed')], ['false', 'true']);
      await vp.keyboard.press('Home'); // keyboard: Home / End / arrows move between the photos and keep focus on the thumbnail
      await mainSrc(first);
      assert.strictEqual(await vp.evaluate(() => document.activeElement.dataset.pageGindex), '0');
      await vp.keyboard.press('End');
      await mainSrc(second);
      assert.strictEqual(await vp.evaluate(() => document.activeElement.dataset.pageGindex), '1');
      // add to cart
      const before = Number(await vp.textContent('#cartCount'));
      await vp.click('[data-add-product]');
      await vp.waitForFunction(n => Number(document.querySelector('#cartCount').textContent) === n + 1, before);
      assert.ok(await vp.evaluate(id => JSON.parse(localStorage.getItem('fakhama-cart-v1') || '[]').some(i => String(i.id) === String(id)), galleryProduct.id), 'saved in the cart');
      await vp.click('#cartOpen');
      await vp.waitForSelector('#cartDrawer.open');
      assert.ok((await vp.textContent('#cartItems')).includes(GALLERY_NAME), 'the cart lists the product');
      await vp.keyboard.press('Escape');
      await vp.waitForFunction(() => !document.querySelector('#cartDrawer.open'));
      // share + copy link
      assert.ok((await vp.getAttribute('.product-page-actions a[href^="https://wa.me/"]', 'href')).startsWith('https://wa.me/?text='), 'share link names no phone number');
      const copy = vp.locator('[data-copy-link]');
      assert.strictEqual(await copy.count(), 1, 'copy-link button');
      assert.strictEqual(await copy.getAttribute('data-copy-link'), url);
      await copy.click();
      await toastSays(vp, 'تم نسخ رابط المنتج');
      assert.strictEqual(await vp.evaluate(() => navigator.clipboard.readText()), url, 'the clipboard holds the product URL');
      await settled(vp); await vp.screenshot({ path: path.join(SHOTS, '21-product-page-desktop.png') });
    })();

    await check('related products on a product page open the quick view; closing it (button or Escape) returns to the product URL and focus', async () => {
      const resp = await vp.goto(base + '/product/wavy-03');
      assert.strictEqual(resp.status(), 200);
      await catalogReady(vp);
      const card = vp.locator('#relatedGrid .product-card').first();
      await card.waitFor();
      const name = (await card.locator('.product-name').textContent()).trim();
      const trigger = card.locator('.quick-add');
      await trigger.click({ force: true });
      await vp.waitForSelector('#productModal.open');
      assert.strictEqual((await vp.textContent('#productModalTitle')).trim(), name);
      const opened = new URL(vp.url()).pathname;
      assert.ok(opened.startsWith('/product/') && opened !== '/product/wavy-03', 'URL follows the open product: ' + opened);
      await vp.click('#productModal [data-close-modal]');
      await vp.waitForFunction(() => !document.querySelector('#productModal.open'));
      await vp.waitForFunction(() => location.pathname === '/product/wavy-03');
      assert.ok((await vp.title()).includes('موديل 3'), 'title restored: ' + await vp.title());
      // keyboard: open with Enter, close with Escape, focus goes back to the button that opened it
      await trigger.focus();
      await vp.keyboard.press('Enter');
      await vp.waitForSelector('#productModal.open');
      await vp.keyboard.press('Escape');
      await vp.waitForFunction(() => !document.querySelector('#productModal.open'));
      await vp.waitForFunction(() => location.pathname === '/product/wavy-03');
      assert.ok(await trigger.evaluate(el => el === document.activeElement), 'focus returns to the quick-view button');
      assert.strictEqual(await vp.locator('#productTitle').count(), 1, 'still on the product page');
    })();

    const CAT_DESC = 'وصف تجريبي لقسم الستائر <من اختبار الواجهة> & يظهر أعلى صفحة القسم';
    await check('category page lists only its own products and shows the description saved in the admin categories page (as text)', async () => {
      await ap.evaluate(() => navigate('categories'));
      await ap.waitForSelector('[data-edit-category="curtains"]');
      await ap.click('[data-edit-category="curtains"]');
      await ap.waitForSelector('#categoryForm');
      await ap.fill('#categoryDescription', CAT_DESC);
      await ap.click('#categoryForm button[type=submit]');
      await toastSays(ap, 'تم تحديث القسم');
      await ap.waitForFunction(t => [...document.querySelectorAll('#categoryGrid .cat-desc')].some(p => p.textContent === t), CAT_DESC);
      const cat = (await (await fetch(base + '/api/categories')).json()).categories.find(c => c.slug === 'curtains');
      assert.strictEqual(cat.description, CAT_DESC);
      const all = await apiProducts();
      const own = all.filter(p => p.category === 'curtains'), others = all.filter(p => p.category !== 'curtains');
      assert.ok(others.some(p => p.name === GALLERY_NAME), 'another category has a product');
      const resp = await vp.goto(base + '/category/curtains');
      assert.strictEqual(resp.status(), 200);
      assert.strictEqual(await vp.getAttribute('body', 'data-category'), 'curtains');
      await oneH1(vp, cat.name);
      assert.strictEqual((await vp.textContent('.page-hero-intro')).trim(), CAT_DESC, 'the description, escaped');
      const cards = async () => vp.$$eval('#productGrid .product-card', els => els.map(c => [c.querySelector('.product-name').textContent.trim(), c.querySelector('.product-category').textContent.trim()]));
      const serverCards = await cards();
      await catalogReady(vp);
      for (const shown of [serverCards, await cards()]) { // the server-rendered grid, then the live one
        assert.strictEqual(shown.length, own.length, 'cards: ' + shown.length);
        assert.ok(shown.every(([, c]) => c === cat.name), 'only this category');
        assert.ok(!shown.some(([n]) => others.some(o => o.name === n)), 'no product of another category');
      }
      assert.strictEqual(await vp.locator('#filterList').count(), 0, 'no category chips on a category page');
      assert.deepStrictEqual(await vp.$$eval('.category-switch a[aria-current="page"]', as => as.map(a => a.getAttribute('href'))), ['/category/curtains']);
      assert.deepStrictEqual(await currentNav(vp), ['/categories']);
      await settled(vp); await vp.screenshot({ path: path.join(SHOTS, '22-category-desktop.png') });
      // and the other way round: the gallery product's category shows it and nothing from curtains
      await vp.goto(base + '/category/blinds');
      await catalogReady(vp);
      const blinds = await cards();
      assert.deepStrictEqual(blinds.map(([n]) => n).sort(), all.filter(p => p.category === 'blinds').map(p => p.name).sort());
      assert.ok(blinds.some(([n]) => n === GALLERY_NAME));
    })();

    const ABOUT = ['فقرة تجريبية أولى من اختبار الواجهة.', 'فقرة تجريبية ثانية <ليست وسمًا> من الاختبار.'];
    await check('/about shows the text saved in the admin Settings page ("صفحة من نحن"); contact details stay as stored, no WhatsApp UI', async () => {
      await ap.evaluate(() => navigate('settings'));
      await ap.waitForSelector('#aboutForm');
      await ap.fill('#sAboutBody', ABOUT.join('\n\n'));
      await ap.click('#aboutForm button[type=submit]');
      await toastSays(ap, 'تم حفظ صفحة من نحن');
      const resp = await vp.goto(base + '/about');
      assert.strictEqual(resp.status(), 200);
      await oneH1(vp);
      assert.deepStrictEqual(await vp.$$eval('.about-story .prose p', ps => ps.map(p => p.textContent.trim())), ABOUT);
      assert.deepStrictEqual(await currentNav(vp), ['/about']);
      const store = await (await fetch(base + '/api/store')).json();
      assert.strictEqual(store.phone, '+966 57 648 6491');
      assert.strictEqual(store.map_url, 'https://maps.app.goo.gl/kn2kxuPujQT9raLG7?g_st=awb');
      assert.ok(!store.whatsapp, 'no WhatsApp number is stored');
      assert.ok((await vp.textContent('main')).includes(store.phone), 'the stored phone is shown');
      assert.strictEqual(await vp.locator('main a[href*="wa.me"]').count(), 0, 'no WhatsApp link while settings.whatsapp is empty');
      await settled(vp); await vp.screenshot({ path: path.join(SHOTS, '23-about-desktop.png'), fullPage: true });
    })();

    await check('contact form honeypot is invisible, aria-hidden and skipped by Tab', async () => {
      const resp = await mp.goto(base + '/contact');
      assert.strictEqual(resp.status(), 200);
      await oneH1(mp, 'تواصل معنا');
      assert.deepStrictEqual(await currentNav(mp), ['/contact']);
      const hp = await mp.$eval('#contactWebsite', el => {
        // the field sits in a wrapper that clips it to 1px and is fully transparent: measure what can actually be seen
        let clip = el.parentElement;
        while (clip && getComputedStyle(clip).overflow !== 'hidden') clip = clip.parentElement;
        const r = clip ? clip.getBoundingClientRect() : el.getBoundingClientRect();
        return { clipped: Boolean(clip && clip.contains(el) && clip !== document.body), area: r.width * r.height, tabIndex: el.tabIndex,
                 ariaHidden: Boolean(el.closest('[aria-hidden="true"]')),
                 visible: el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true, opacityProperty: true, visibilityProperty: true }) };
      });
      assert.deepStrictEqual(hp, { clipped: true, area: hp.area, tabIndex: -1, ariaHidden: true, visible: false }, JSON.stringify(hp));
      assert.ok(hp.area <= 1, 'no visible area: ' + hp.area);
      await mp.focus('#contactMessage');
      await mp.keyboard.press('Tab');
      assert.strictEqual(await mp.evaluate(() => document.activeElement.name), 'consent', 'Tab goes from the message to the consent box');
      await mp.keyboard.press('Shift+Tab');
      assert.strictEqual(await mp.evaluate(() => document.activeElement.id), 'contactMessage');
      assert.strictEqual(await mp.locator('main a[href*="wa.me"]').count(), 0, 'no WhatsApp link while settings.whatsapp is empty');
    })();

    const MSG = { name: 'زائر اختبار التواصل', phone: '0551234567', email: 'visitor@example.com', message: 'رسالة تجريبية من اختبار الواجهة لصفحة التواصل.' };
    await check('/contact message reaches the admin Messages page with the new-message badge; mark handled, then delete', async () => {
      await mp.fill('#contactName', MSG.name);
      await mp.fill('#contactPhone', MSG.phone);
      await mp.fill('#contactEmail', MSG.email);
      await mp.fill('#contactMessage', MSG.message);
      await mp.check('#contactForm input[name=consent]');
      await mp.click('#contactForm button[type=submit]');
      await mp.waitForSelector('#contactStatus[data-state="success"]');
      assert.ok((await mp.textContent('#contactStatus')).includes('وصلت رسالتك'));
      assert.strictEqual(await mp.inputValue('#contactName'), '', 'the form is cleared after sending');
      await settled(mp); await mp.screenshot({ path: path.join(SHOTS, '24-contact-sent-mobile.png') });
      // admin: badge + dashboard notice, then the inbox
      await ap.evaluate(() => navigate('overview'));
      await ap.waitForSelector('.stats-grid');
      await ap.waitForSelector('#messagesBadge:not(.hidden)', { state: 'attached' });
      assert.strictEqual((await ap.textContent('#messagesBadge [aria-hidden="true"]')).trim(), '1');
      assert.ok((await ap.textContent('#messagesBadge')).includes('رسالة جديدة واحدة'), 'screen readers hear the count');
      assert.strictEqual(await ap.locator('a.msg-alert[href="#messages/new"]').count(), 1, 'dashboard notice');
      await ap.evaluate(() => navigate('messages'));
      const card = () => ap.locator('#messagesList .msg-card', { hasText: MSG.name });
      await card().waitFor();
      const text = await card().textContent();
      assert.ok(text.includes(MSG.message) && text.includes(MSG.email) && text.includes('صفحة التواصل'), text);
      assert.strictEqual((await card().locator('.msg-head .badge').textContent()).trim(), 'جديدة');
      assert.strictEqual(await card().locator(`a[href="tel:${MSG.phone}"]`).count(), 1, 'call link');
      await settled(ap); await ap.screenshot({ path: path.join(SHOTS, '25-admin-messages-mobile.png') });
      await card().locator('[data-msg-status="handled"]').click();
      await toastSays(ap, 'تم تحديد الرسالة كمعالجة');
      await ap.waitForFunction(n => [...document.querySelectorAll('#messagesList .msg-card')].some(c => c.textContent.includes(n) && c.querySelector('.msg-head .badge').textContent.includes('تمت المعالجة')), MSG.name);
      await ap.waitForSelector('#messagesBadge.hidden', { state: 'attached' });
      ap.once('dialog', d => d.accept());
      await card().locator('[data-msg-delete]').click();
      await toastSays(ap, 'تم حذف الرسالة');
      await ap.waitForFunction(n => !document.querySelector('#pageContent').textContent.includes(n), MSG.name);
      assert.strictEqual((await ap.evaluate(() => api('/api/admin/messages?status=all'))).messages.length, 0, 'deleted on the server');
    })();

    await check('unknown pages get the branded 404 (HTTP 404, noindex, one h1, search box); page URLs drop a trailing slash (301)', async () => {
      const resp = await mp.goto(base + '/no-such-page');
      assert.strictEqual(resp.status(), 404);
      assert.strictEqual(await mp.getAttribute('body', 'data-page'), 'notfound');
      await oneH1(mp, 'الصفحة غير موجودة');
      assert.ok((await mp.getAttribute('meta[name=robots]', 'content')).includes('noindex'));
      assert.deepStrictEqual(await currentNav(mp), [], 'no menu link is current');
      assert.deepStrictEqual(await mp.$$eval('.notfound-links a', as => as.map(a => a.getAttribute('href'))), ['/', '/products', '/categories', '/faq', '/contact']);
      await settled(mp); await mp.screenshot({ path: path.join(SHOTS, '26-not-found-mobile.png') });
      await mp.fill('#notFoundSearch', 'FAL-WAV-005');
      await follow(mp, () => mp.press('#notFoundSearch', 'Enter'));
      assert.deepStrictEqual(pathAndQ(mp.url()), ['/products', 'FAL-WAV-005']);
      await catalogReady(mp);
      assert.strictEqual(await mp.locator('#productGrid .product-card').count(), 1);
      const gone = await mp.goto(base + '/product/no-such-product');
      assert.strictEqual(gone.status(), 404);
      await oneH1(mp, 'الصفحة غير موجودة');
      assert.ok((await mp.textContent('.page-hero-intro')).includes('هذا المنتج غير متاح'));
      for (const [from, to] of [['/about/', '/about'], ['/category/curtains/', '/category/curtains']]) {
        const r = await fetch(base + from, { redirect: 'manual' });
        assert.strictEqual(r.status, 301, from);
        assert.strictEqual(r.headers.get('location'), to, from);
      }
    })();

    await check('header search on a non-shop page goes to /products?q= (phone search toggle and desktop)', async () => {
      const all = await apiProducts();
      await mp.goto(base + '/faq');
      await mp.click('#searchToggle');
      await mp.fill('#searchInput', 'FAL-WAV-005');
      const resp = await follow(mp, () => mp.press('#searchInput', 'Enter'));
      assert.strictEqual(resp.status(), 200);
      assert.deepStrictEqual(pathAndQ(mp.url()), ['/products', 'FAL-WAV-005']);
      await catalogReady(mp);
      assert.strictEqual(await mp.locator('#productGrid .product-card').count(), 1);
      assert.strictEqual(await mp.inputValue('#searchInput'), 'FAL-WAV-005');
      assert.deepStrictEqual(await currentNav(mp), ['/products']);
      await vp.goto(base + '/about');
      await vp.fill('#searchInput', 'موديل 3');
      await follow(vp, () => vp.press('#searchInput', 'Enter'));
      assert.deepStrictEqual(pathAndQ(vp.url()), ['/products', 'موديل 3']);
      await catalogReady(vp);
      assert.strictEqual(await vp.locator('#productGrid .product-card').count(), searchCount(all, 'موديل 3'));
      await vp.goto(base + '/contact'); // an empty search opens the full list
      await follow(vp, () => vp.press('#searchInput', 'Enter'));
      assert.deepStrictEqual(pathAndQ(vp.url()), ['/products', null]);
    })();
    await visitor.close();
    await mobile.close();
    await check('admin categories: add with image, reorder, edit, delete', async () => {
      await ap.evaluate(() => navigate('categories'));
      await ap.waitForSelector('#categoryAddForm');
      await ap.fill('#newCategoryName', 'قسم اختبار الواجهة');
      await ap.setInputFiles('#newCategoryImage', png);
      await ap.click('#categoryAddForm button[type=submit]');
      await ap.waitForFunction(() => document.querySelector('#categoryGrid').textContent.includes('قسم اختبار الواجهة'));
      const before = await ap.$$eval('#categoryGrid .category-info b', els => els.map(e => e.textContent));
      await ap.locator('#categoryGrid .cat-row', { hasText: 'قسم اختبار الواجهة' }).locator('[data-move=up]').click();
      await ap.waitForFunction((prev) => { const now = [...document.querySelectorAll('#categoryGrid .category-info b')].map(e => e.textContent); return now.join() !== prev.join(); }, before);
      const hasImage = await ap.locator('#categoryGrid .cat-row', { hasText: 'قسم اختبار الواجهة' }).locator('img').count();
      assert.strictEqual(hasImage, 1, 'category image shown');
      await ap.locator('#categoryGrid .cat-row', { hasText: 'قسم اختبار الواجهة' }).locator('[data-edit-category]').click();
      await ap.fill('#categoryName', 'قسم اختبار معدّل');
      await ap.click('#categoryForm button[type=submit]');
      await ap.waitForFunction(() => document.querySelector('#categoryGrid').textContent.includes('قسم اختبار معدّل'));
      ap.once('dialog', d => d.accept());
      await ap.locator('#categoryGrid .cat-row', { hasText: 'قسم اختبار معدّل' }).locator('[data-delete-category]').click();
      await ap.waitForFunction(() => !document.querySelector('#categoryGrid').textContent.includes('قسم اختبار معدّل'));
      await ap.evaluate(() => navigate('products'));
      await ap.waitForSelector('#productsBody .thumb');
    })();
    await check('deep link /admin/#products/new opens the add-product form after login', async () => {
      const dl = await admin.newPage();
      await dl.goto(base + '/admin/#products/new');
      await dl.waitForSelector('#productForm', { timeout: 15000 });
      await dl.close();
    })();
    await check('admin hides, edits and deletes a product', async () => {
      const row = ap.locator('#productsBody tr', { hasText: 'ستارة اختبار الرفع' });
      await row.locator('[data-toggle-product]').click();
      await ap.waitForFunction(() => [...document.querySelectorAll('#productsBody tr')].some(r => r.textContent.includes('ستارة اختبار الرفع') && r.textContent.includes('مخفي')));
      await ap.locator('#productsBody tr', { hasText: 'ستارة اختبار الرفع' }).locator('[data-edit-product]').click();
      await ap.fill('#pPrice', '333');
      await ap.click('#productForm button[type=submit]');
      await ap.waitForFunction(() => [...document.querySelectorAll('#productsBody tr')].some(r => r.textContent.includes('333') || r.textContent.includes('٣٣٣')));
      ap.once('dialog', d => d.accept());
      await ap.locator('#productsBody tr', { hasText: 'ستارة اختبار الرفع' }).locator('[data-delete-product]').click();
      await ap.waitForFunction(() => !document.querySelector('#productsBody').textContent.includes('ستارة اختبار الرفع'));
    })();
    await check('admin orders page, status change and settings save', async () => {
      await ap.evaluate(() => navigate('orders'));
      await ap.waitForSelector('.order-status');
      await ap.screenshot({ path: path.join(SHOTS, '09-admin-orders-mobile.png') });
      await ap.selectOption('.order-status >> nth=0', 'confirmed');
      await ap.waitForFunction(() => document.querySelector('#toast').textContent.includes('تم تحديث'));
      await ap.evaluate(() => navigate('settings'));
      await ap.waitForSelector('#settingsForm');
      await ap.fill('#sTagline', 'عبارة محدّثة من الاختبار');
      await ap.click('#settingsForm button[type=submit]');
      await ap.waitForFunction(() => document.querySelector('#toast').textContent.includes('تم حفظ'));
      const store = await (await fetch(base + '/api/store')).json();
      assert.strictEqual(store.tagline, 'عبارة محدّثة من الاختبار');
      assert.strictEqual(store.phone, '+966 57 648 6491');
      assert.strictEqual(store.map_url, 'https://maps.app.goo.gl/kn2kxuPujQT9raLG7?g_st=awb');
    })();
    await check('admin changes the password from the UI (success toast, no JS error)', async () => {
      await ap.evaluate(() => navigate('settings'));
      await ap.waitForSelector('#passwordForm');
      newPassword = 'P' + crypto.randomBytes(14).toString('base64url');
      await ap.fill('#oldPassword', password);
      await ap.fill('#newPassword', newPassword);
      await ap.fill('#confirmPassword', newPassword);
      await ap.click('#passwordForm button[type=submit]');
      await ap.waitForFunction(() => document.querySelector('#toast').textContent.includes('تم تحديث كلمة المرور بنجاح'));
      assert.strictEqual(await ap.inputValue('#oldPassword'), '', 'form reset after success');
    })();
    await check('two-factor: set up with an authenticator code, login then requires the code', async () => {
      const totp = (secret) => { // independent RFC 6238 implementation
        const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'; let bits = '';
        for (const ch of secret.replace(/\s+/g, '').toUpperCase()) bits += alphabet.indexOf(ch).toString(2).padStart(5, '0');
        const key = Buffer.from(bits.match(/.{8}/g).map(b => parseInt(b, 2)));
        const counter = Buffer.alloc(8); counter.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 30000)));
        const h = crypto.createHmac('sha1', key).update(counter).digest(); const o = h[h.length - 1] & 15;
        return String((h.readUInt32BE(o) & 0x7fffffff) % 1000000).padStart(6, '0');
      };
      await ap.evaluate(() => navigate('settings'));
      await ap.waitForSelector('#tfStart');
      await ap.click('#tfStart');
      await ap.waitForSelector('#tfEnable');
      const secret = (await ap.textContent('#twofaBody code')).replace(/\s+/g, '');
      await ap.fill('#tfCode', totp(secret));
      await ap.click('#tfEnable');
      await ap.waitForSelector('#tfDisable');
      await ap.click('#menuButton'); await ap.click('#logoutBtn');
      await ap.waitForSelector('#loginScreen:not(.hidden)');
      await ap.fill('#loginUser', 'admin'); await ap.fill('#loginPass', newPassword);
      await ap.click('#loginForm button[type=submit]');
      await ap.waitForSelector('#codeField:not(.hidden)');
      await ap.fill('#loginCode', totp(secret));
      await ap.click('#loginForm button[type=submit]');
      await ap.waitForSelector('#appShell:not(.hidden)'); // lands on the page named in the URL hash (#settings)
    })();
    await check('admin logout returns to the login screen', async () => {
      await ap.click('#menuButton'); // the sidebar (with the logout button) is a drawer on phones
      await ap.click('#logoutBtn');
      await ap.waitForSelector('#loginScreen:not(.hidden)');
      assert.strictEqual((await fetch(base + '/api/admin/dashboard')).status, 401);
    })();

    // ------------------------------------------------------------ desktop admin screenshot
    const desk = await browser.newContext({ viewport: { width: 1366, height: 800 }, locale: 'ar-SA' });
    const dp = await desk.newPage();
    await dp.goto(base + '/');
    await dp.waitForSelector('.product-card');
    await dp.screenshot({ path: path.join(SHOTS, '10-store-desktop.png') });
    // ------------------------------------------------------------ failure modes
    await check('catalogue error state shows a retry button, blocks checkout, then recovers (no invented products)', async () => {
      const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, serviceWorkers: 'block', locale: 'ar-SA' });
      const pg = await ctx.newPage();
      await pg.route('**/api/products', route => route.abort());
      await pg.goto(base + '/');
      await pg.waitForSelector('[data-retry-catalog]');
      assert.strictEqual(await pg.locator('.product-card').count(), 0, 'no fake products');
      await pg.screenshot({ path: path.join(SHOTS, '11-store-api-error-mobile.png') });
      await pg.unroute('**/api/products');
      await pg.click('[data-retry-catalog]');
      await pg.waitForSelector('.product-card');
      await ctx.close();
    })();
    await check('server down: navigation falls back to the static offline page', async () => {
      const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, locale: 'ar-SA' });
      const pg = await ctx.newPage();
      await pg.goto(base + '/');
      await pg.waitForSelector('.product-card');
      await pg.evaluate(async () => { await navigator.serviceWorker.ready; });
      await pg.reload(); // now controlled by the SW
      await pg.waitForSelector('.product-card');
      const cached = await pg.evaluate(async () => { const out = []; for (const k of await caches.keys()) for (const r of await (await caches.open(k)).keys()) out.push(new URL(r.url).pathname); return out; });
      assert.ok(cached.length > 0 && cached.every(p => !p.startsWith('/api/') && !p.startsWith('/admin') && !p.startsWith('/uploads/') && p !== '/'), 'cache contains: ' + cached.join(','));
      server.kill();
      await sleep(500);
      await pg.goto(base + '/', { waitUntil: 'domcontentloaded' });
      await pg.waitForSelector('#retry', { timeout: 8000 });
      assert.ok((await pg.textContent('h1')).includes('لا يوجد اتصال'));
      await pg.screenshot({ path: path.join(SHOTS, '04-offline-mobile.png') });
      await ctx.close();
    })();
  } finally {
    await browser.close();
    server.kill();
    fs.rmSync(dataDir, { recursive: true, force: true });
  }

  for (const [status, name] of results) console.log(status, name);
  const unexpected = errors.filter(e => !/net::ERR_|admin http 401: .*\/api\/admin\/login/i.test(e) && !/requestfailed: .*\/api\//.test(e));
  console.log('\nbrowser errors:', unexpected.length ? '\n  ' + unexpected.join('\n  ') : 'none');
  console.log('screenshots:', SHOTS);
  process.exit(results.some(r => r[0] === 'FAIL') || unexpected.length ? 1 : 0);
})();
