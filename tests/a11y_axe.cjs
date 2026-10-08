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
  const audit = async (page, name) => {
    await page.evaluate(axeSource);
    const res = await page.evaluate(() => axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa', 'best-practice'] } }));
    const v = res.violations;
    console.log(`${v.length ? 'FAIL' : 'PASS'} ${name} — ${v.length} violation(s), ${res.passes.length} rules passed`);
    for (const x of v) { failures++; console.log(`   [${x.impact}] ${x.id}: ${x.help} (${x.nodes.length} node(s)) e.g. ${x.nodes[0].target.join(' ')} — ${(x.nodes[0].failureSummary || '').split('\n').slice(0, 2).join(' ')}`); }
  };
  try {
    for (const [label, vp] of [['phone', { width: 390, height: 844 }], ['desktop', { width: 1366, height: 800 }]]) {
      const ctx = await browser.newContext({ viewport: vp, locale: 'ar-SA' }); const page = await ctx.newPage();
      await page.goto(base + '/'); await page.waitForSelector('.product-card'); await page.waitForTimeout(500);
      await audit(page, `storefront (${label})`);
      await page.locator('.quick-add').first().click({ force: true }); await page.waitForSelector('#productModal.open'); await page.waitForTimeout(300);
      await audit(page, `product modal (${label})`);
      await page.keyboard.press('Escape');
      await page.goto(base + '/privacy'); await audit(page, `privacy page (${label})`);
      await page.goto(base + '/admin/'); await page.waitForSelector('#loginForm'); await audit(page, `admin login (${label})`);
      await page.fill('#loginUser', 'admin'); await page.fill('#loginPass', password); await page.click('#loginForm button[type=submit]'); await page.waitForSelector('.stats-grid');
      await audit(page, `admin dashboard (${label})`);
      for (const p of ['products', 'orders', 'categories', 'settings']) { await page.evaluate(x => navigate(x), p); await page.waitForTimeout(700); await audit(page, `admin ${p} (${label})`); }
      await page.evaluate(() => navigate('products')); await page.waitForSelector('#addProduct'); await page.click('#addProduct'); await page.waitForSelector('#productForm'); await audit(page, `admin product form (${label})`);
      await ctx.close();
    }
  } finally { await browser.close(); srv.kill(); fs.rmSync(dir, { recursive: true, force: true }); }
  console.log(failures ? `\n${failures} violation group(s)` : '\nno axe violations'); process.exit(failures ? 1 : 0);
})();
