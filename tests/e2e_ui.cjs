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
    cwd: dataDir, env: { ...process.env, PORT: String(port), HOST: '127.0.0.1', DATA_DIR: dataDir, ADMIN_PASSWORD: password }, stdio: ['ignore', 'pipe', 'pipe'],
  });
  let serverLog = '';
  server.stderr.on('data', d => { serverLog += d; });
  for (let i = 0; i < 100; i++) { try { await fetch(`http://127.0.0.1:${port}/api/health`); break; } catch { await sleep(100); } }
  const base = `http://127.0.0.1:${port}`;

  const browser = await chromium.launch();
  const results = [];
  const check = (name, fn) => async () => { try { await fn(); results.push(['PASS', name]); } catch (e) { results.push(['FAIL', name + ' — ' + e.message.split('\n')[0]]); } };
  const errors = [];

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
    await check('storefront lists 8 live products', async () => assert.strictEqual(await page.locator('.product-card').count(), 8))();
    await check('all product images decode', async () => {
      const broken = await page.$$eval('img.product-image', imgs => imgs.filter(i => !i.complete || i.naturalWidth === 0).length);
      assert.strictEqual(broken, 0);
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
      await page.fill('#searchInput', 'رول');
      await page.waitForTimeout(150);
      const n = await page.locator('.product-card').count();
      assert.ok(n >= 1 && n < 8, 'cards=' + n);
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
      assert.ok((await ap.textContent('.dashboard-grid')).includes('MH-'));
      await ap.screenshot({ path: path.join(SHOTS, '06-admin-dashboard-mobile.png') });
    })();
    await check('admin product thumbnails load (relative paths used to 404 under /admin/)', async () => {
      await ap.evaluate(() => navigate('products'));
      await ap.waitForSelector('#productsBody .thumb');
      await ap.waitForTimeout(500);
      const broken = await ap.$$eval('#productsBody img.thumb', imgs => imgs.filter(i => !i.complete || i.naturalWidth === 0).length);
      assert.strictEqual(broken, 0);
      await ap.screenshot({ path: path.join(SHOTS, '07-admin-products-mobile.png') });
    })();
    const png = path.join(dataDir, 'upload-test.png');
    fs.writeFileSync(png, PNG);
    await check('admin adds a product with an uploaded image, storefront shows it', async () => {
      await ap.click('#addProduct');
      await ap.waitForSelector('#productForm');
      await ap.setInputFiles('#productImage', png);
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
      assert.ok(made && /^\/uploads\/[0-9a-f]{32}\.png$/.test(made.image), JSON.stringify(made));
      assert.strictEqual((await fetch(base + made.image)).status, 200);
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
      const newPassword = 'P' + crypto.randomBytes(14).toString('base64url');
      await ap.fill('#oldPassword', password);
      await ap.fill('#newPassword', newPassword);
      await ap.fill('#confirmPassword', newPassword);
      await ap.click('#passwordForm button[type=submit]');
      await ap.waitForFunction(() => document.querySelector('#toast').textContent.includes('تم تحديث كلمة المرور بنجاح'));
      assert.strictEqual(await ap.inputValue('#oldPassword'), '', 'form reset after success');
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
