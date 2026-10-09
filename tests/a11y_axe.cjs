// Automated accessibility audit (axe-core, WCAG 2.2 A/AA rules) of the main screens, on phone and desktop sizes.
//   npm i axe-core   (anywhere);   NODE_PATH=$(npm root -g):/path/to/node_modules node tests/a11y_axe.cjs
// Starts its own throw-away server; exits non-zero when any violation is found.
const { chromium } = require('playwright');
const { spawn } = require('child_process');
const fs = require('fs'); const os = require('os'); const path = require('path'); const net = require('net');
const axeSource = fs.readFileSync(require.resolve('axe-core/axe.min.js'), 'utf8');
const freePort = () => new Promise(r => { const s = net.createServer(); s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => r(p)); }); });
(async () => {
  const port = await freePort(); const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'axe-')); const password = 'Ax' + Math.random().toString(36).slice(2) + 'Zz9!long';
  const srv = spawn('python3', ['-I', path.resolve(__dirname, '..', 'app.py')], { cwd: dir, env: { ...process.env, PORT: String(port), DATA_DIR: dir, ADMIN_PASSWORD: password, ENV_FILE: 'off' }, stdio: 'ignore' });
  for (let i = 0; i < 100; i++) { try { await fetch(`http://127.0.0.1:${port}/api/health`); break; } catch { await new Promise(r => setTimeout(r, 100)); } }
  const base = `http://127.0.0.1:${port}`; const browser = await chromium.launch(); let failures = 0;
  const fail = message => { failures++; console.log('FAIL ' + message); };
  const audit = async (page, name) => {
    // Measure the settled page: wait for entrance animations (finite ones; the looping shimmer never ends).
    await page.evaluate(() => Promise.all(document.getAnimations().filter(a => Number.isFinite(a.effect?.getComputedTiming().endTime)).map(a => a.finished.catch(() => {}))));
    await page.evaluate(axeSource);
    const res = await page.evaluate(() => axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa', 'best-practice'] } }));
    const v = res.violations;
    console.log(`${v.length ? 'FAIL' : 'PASS'} ${name} — ${v.length} violation(s), ${res.passes.length} rules passed`);
    for (const x of v) { failures++; console.log(`   [${x.impact}] ${x.id}: ${x.help} (${x.nodes.length} node(s)) e.g. ${x.nodes[0].target.join(' ')} — ${(x.nodes[0].failureSummary || '').split('\n').slice(0, 2).join(' ')}`); }
  };
  // Sections fade in as they scroll into view; scroll through so axe sees every one of them, then wait for the motion to settle.
  const revealAll = async (page, name) => {
    await page.evaluate(async () => { for (let y = 0; y < document.documentElement.scrollHeight; y += 300) { scrollTo({ top: y, behavior: 'instant' }); await new Promise(r => setTimeout(r, 60)); } scrollTo({ top: 0, behavior: 'instant' }); });
    await page.waitForTimeout(1500);
    const hidden = await page.evaluate(() => document.querySelectorAll('.rv:not(.in)').length);
    if (hidden) fail(`${name} — ${hidden} section(s) never revealed`);
  };
  // The storefront script loads the live catalogue on every page and re-renders the grids with it: audit after that.
  const catalogReady = page => page.waitForFunction(() => typeof catalogState !== 'undefined' && catalogState === 'ready');
  const adminLogin = async page => {
    await page.fill('#loginUser', 'admin'); await page.fill('#loginPass', password); await page.click('#loginForm button[type=submit]'); await page.waitForSelector('.stats-grid');
  };
  try {
    // Content the new pages show, created the way the owner would: a product with two photos (gallery thumbnails),
    // a category description, the About text, and customer messages in both states (new and handled) for the inbox.
    const setup = await browser.newContext(); const sp = await setup.newPage();
    await sp.goto(base + '/admin/'); await sp.waitForSelector('#loginForm'); await adminLogin(sp);
    const contact = (name, message) => fetch(base + '/api/contact', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, phone: '0551234567', email: 'visitor@example.com', message, page: '/contact', consent: true, website: '' }) });
    for (const [name, message] of [['زائر فحص الوصول', 'رسالة تجريبية جديدة لفحص إمكانية الوصول.'], ['زائر ثانٍ', 'رسالة تجريبية ستُحدَّد كمعالجة.']]) {
      const r = await contact(name, message); if (r.status !== 201) fail(`setup: contact message → HTTP ${r.status}`);
    }
    const gallerySlug = await sp.evaluate(async () => {
      const made = await api('/api/admin/products', { method: 'POST', body: { name: 'ستارة فحص الوصول', category: 'curtains', price: 0, stock: 5, active: true,
        description: 'منتج تجريبي بصورتين لفحص إمكانية الوصول.', images: [{ url: '/assets/wavy-01.jpg', thumb: '/assets/wavy-01-t.jpg' }, { url: '/assets/wavy-02.jpg', thumb: '/assets/wavy-02-t.jpg' }] } });
      const cat = (await api('/api/admin/categories')).categories.find(c => c.slug === 'curtains');
      await api('/api/admin/categories/curtains', { method: 'PUT', body: { name: cat.name, active: true, description: 'وصف تجريبي للقسم لفحص إمكانية الوصول.' } });
      await api('/api/admin/settings', { method: 'PUT', body: { about_body: 'فقرة تجريبية أولى لفحص إمكانية الوصول.\n\nفقرة تجريبية ثانية.' } });
      const inbox = await api('/api/admin/messages?status=all');
      await api(`/api/admin/messages/${inbox.messages.find(m => m.name === 'زائر ثانٍ').id}`, { method: 'PATCH', body: { status: 'handled' } });
      return made.product.images.length === 2 ? made.product.slug : '';
    });
    if (!gallerySlug) fail('setup: the two-photo product was not created');
    await setup.close();

    // [audit name, URL, expected HTTP status]
    const PAGES = [
      ['products page', '/products', 200],
      ['products search without results', '/products?q=' + encodeURIComponent('لا يوجد منتج بهذا الاسم'), 200],
      ['categories page', '/categories', 200],
      ['category page', '/category/curtains', 200],
      ['product page (two photos)', '/product/' + encodeURIComponent(gallerySlug), 200],
      ['about page', '/about', 200],
      ['contact page', '/contact', 200],
      ['FAQ page (answers open)', '/faq', 200],
      ['404 page', '/no-such-page', 404],
    ];
    for (const [label, vp] of [['phone', { width: 390, height: 844 }], ['desktop', { width: 1366, height: 800 }]]) {
      const ctx = await browser.newContext({ viewport: vp, locale: 'ar-SA' }); const page = await ctx.newPage();
      await page.goto(base + '/'); await page.waitForSelector('.product-card'); await page.waitForTimeout(500);
      await revealAll(page, `storefront (${label})`);
      await audit(page, `storefront (${label})`);
      await page.locator('.quick-add').first().click({ force: true }); await page.waitForSelector('#productModal.open'); await page.waitForTimeout(300);
      await audit(page, `product modal (${label})`);
      await page.keyboard.press('Escape');
      for (const [name, url, status] of PAGES) {
        const resp = await page.goto(base + url);
        if (!resp || resp.status() !== status) fail(`${name} (${label}) — HTTP ${resp && resp.status()}, expected ${status}`);
        await catalogReady(page);
        if (url === '/faq') await page.$$eval('main details', ds => ds.forEach(d => { d.open = true; }));
        await revealAll(page, `${name} (${label})`);
        await audit(page, `${name} (${label})`);
        if (url === '/contact') { // the form's success state as well
          await page.fill('#contactName', 'زائر فحص الوصول'); await page.fill('#contactPhone', '0551234567'); await page.fill('#contactMessage', 'رسالة تجريبية لفحص حالة الإرسال.');
          await page.check('#contactForm input[name=consent]'); await page.click('#contactForm button[type=submit]');
          await page.waitForSelector('#contactStatus[data-state="success"]');
          await audit(page, `contact page after sending (${label})`);
        }
      }
      await page.goto(base + '/privacy'); await audit(page, `privacy page (${label})`);
      await page.goto(base + '/admin/'); await page.waitForSelector('#loginForm'); await audit(page, `admin login (${label})`);
      await adminLogin(page);
      await audit(page, `admin dashboard (${label})`);
      for (const p of ['products', 'orders', 'messages', 'categories', 'settings']) {
        await page.evaluate(x => navigate(x), p); await page.waitForTimeout(700);
        if (p === 'messages') {
          await page.waitForSelector('#messagesList .msg-card');
          const states = await page.$$eval('#messagesList .msg-card', cards => new Set(cards.map(c => c.classList.contains('is-new'))).size);
          if (states !== 2) fail(`admin messages (${label}) — expected new and handled messages on the page`);
        }
        await audit(page, `admin ${p} (${label})`);
      }
      await page.evaluate(() => navigate('products')); await page.waitForSelector('#addProduct'); await page.click('#addProduct'); await page.waitForSelector('#productForm'); await audit(page, `admin product form (${label})`);
      await ctx.close();
    }
  } finally { await browser.close(); srv.kill(); fs.rmSync(dir, { recursive: true, force: true }); }
  console.log(failures ? `\n${failures} violation group(s)` : '\nno axe violations'); process.exit(failures ? 1 : 0);
})();
