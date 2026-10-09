#!/usr/bin/env node
// Playwright (Chromium) renderer used by tools/make_handover.py. Two jobs, one browser:
//
//   NODE_PATH=$(npm root -g) node tools/render_pdf.cjs pdf JOBS.json
//       JOBS.json = [{"html": "/abs/in.html", "pdf": "/abs/out.pdf", "title": "footer title", "landscape": false}, ...]
//       A4 print-to-PDF of local HTML files (RTL, El Messiri headings come from the HTML's own @font-face),
//       background colours kept, a footer with the document title and «صفحة X من Y».
//
//   HANDOVER_ADMIN_PASSWORD=... NODE_PATH=$(npm root -g) node tools/render_pdf.cjs screens CONFIG.json
//       CONFIG.json = {"base": "http://127.0.0.1:PORT", "out": "/abs/dir", "username": "admin",
//                      "widths": [390, 1366], "public": [["name", "/path"], ...], "admin": [["name", "page"], ...]}
//       Full-page PNG screenshots of the storefront pages and the admin screens of a throw-away server.
//       Each page is scrolled to the bottom first: its sections fade in as they enter the screen.
//       The admin password is read from the environment only (never from a file or the command line).
'use strict';
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const sleep = ms => new Promise(r => setTimeout(r, ms));

function footer(title) {
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  return `<div dir="rtl" style="width:100%;margin:0 14mm;font-family:'DejaVu Sans','Segoe UI',Tahoma,sans-serif;font-size:7.5px;color:#5d574c;display:flex;justify-content:space-between;align-items:center;border-top:0.6px solid #c9a464;padding-top:4px;-webkit-print-color-adjust:exact">`
    + `<span>الفخامة للأقمشة والستائر — ${esc(title)}</span>`
    + `<span>صفحة <span class="pageNumber"></span> من <span class="totalPages"></span></span></div>`;
}

async function renderPdfs(jobs) {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    for (const job of jobs) {
      await page.goto('file://' + path.resolve(job.html), { waitUntil: 'load' });
      await page.evaluate(() => document.fonts.ready);
      // pages that compute values on load (contrast ratios in the design system) get a moment to finish
      await page.waitForTimeout(job.settle || 150);
      await page.emulateMedia({ media: 'print' });
      await page.pdf({
        path: job.pdf, format: 'A4', landscape: Boolean(job.landscape), printBackground: true,
        displayHeaderFooter: true, headerTemplate: '<span></span>', footerTemplate: footer(job.title || ''),
        margin: { top: '16mm', bottom: '18mm', left: '14mm', right: '14mm' }, preferCSSPageSize: false,
      });
      await page.emulateMedia({ media: 'screen' });
      console.log('pdf  ' + job.pdf);
    }
  } finally {
    await browser.close();
  }
}

// Scroll through the page so every scroll-revealed section (.rv) gets its .in class, then let the motion settle.
async function revealAll(page) {
  await page.evaluate(async () => {
    const step = Math.max(200, Math.floor(innerHeight * 0.6));
    for (let y = 0; y < document.documentElement.scrollHeight; y += step) {
      scrollTo({ top: y, behavior: 'instant' });
      await new Promise(r => setTimeout(r, 90));
    }
    scrollTo({ top: document.documentElement.scrollHeight, behavior: 'instant' });
    await new Promise(r => setTimeout(r, 200));
    scrollTo({ top: 0, behavior: 'instant' });
  });
  await page.waitForTimeout(1600);
  // finite entrance animations (the looping shimmer never ends, so it is not awaited)
  await page.evaluate(() => Promise.all(document.getAnimations()
    .filter(a => Number.isFinite(a.effect?.getComputedTiming().endTime)).map(a => a.finished.catch(() => {}))));
  await loadImages(page);
}

// A full-page capture must not catch lazy photos half-way: load and decode every <img> first.
async function loadImages(page) {
  await page.evaluate(async () => {
    const images = [...document.images];
    images.forEach(img => { if (img.loading === 'lazy') img.loading = 'eager'; });
    await Promise.all(images.map(img => (img.complete ? Promise.resolve() : new Promise(done => {
      img.addEventListener('load', done, { once: true });
      img.addEventListener('error', done, { once: true });
    })).then(() => (img.naturalWidth ? img.decode().catch(() => {}) : null))));
  });
  await page.waitForLoadState('networkidle');
}

// Park the pointer where nothing reacts to hover (the last click would otherwise leave a tooltip or hover state).
async function parkPointer(page, width) {
  await page.mouse.move(Math.round(width * 0.4), 3);
  await page.waitForTimeout(400);
}

// Admin pages keep the sidebar position:fixed; make the viewport as tall as the page so it spans the whole capture.
async function adminShot(page, width, file) {
  const height = await page.evaluate(() => Math.max(document.documentElement.scrollHeight, document.body.scrollHeight));
  const base = width < 600 ? 844 : 900;
  await page.setViewportSize({ width, height: Math.max(base, height) });
  await page.waitForTimeout(500);
  await loadImages(page);
  await parkPointer(page, width);
  await page.screenshot({ path: file, fullPage: true });
  await page.setViewportSize({ width, height: base });
}

function contextOptions(width) {
  return width < 600
    ? { viewport: { width, height: 844 }, deviceScaleFactor: 1, isMobile: true, hasTouch: true, locale: 'ar-SA' }
    : { viewport: { width, height: 900 }, deviceScaleFactor: 1, locale: 'ar-SA' };
}

async function renderScreens(config) {
  const password = process.env.HANDOVER_ADMIN_PASSWORD || '';
  if (!password) throw new Error('HANDOVER_ADMIN_PASSWORD is not set');
  fs.mkdirSync(config.out, { recursive: true });
  const browser = await chromium.launch();
  const written = [];
  const problems = [];
  try {
    for (const width of config.widths) {
      // 1) storefront pages, as a customer (a fresh context: no admin cookie, so the visits count in the statistics)
      const shop = await browser.newContext(contextOptions(width));
      const page = await shop.newPage();
      page.on('pageerror', e => problems.push(`${width}px pageerror: ${e.message}`));
      for (const [name, url] of config.public) {
        const resp = await page.goto(config.base + url, { waitUntil: 'networkidle' });
        await revealAll(page);
        await parkPointer(page, width);
        const file = path.join(config.out, `${name}-${width}.png`);
        await page.screenshot({ path: file, fullPage: true });
        written.push(file);
        console.log(`png  ${file}  (HTTP ${resp ? resp.status() : '?'})`);
      }
      await shop.close();

      // 2) admin screens: the login screen, then every page after signing in through the form
      const admin = await browser.newContext(contextOptions(width));
      const ap = await admin.newPage();
      ap.on('pageerror', e => problems.push(`${width}px admin pageerror: ${e.message}`));
      await ap.goto(config.base + '/admin/', { waitUntil: 'networkidle' });
      await ap.waitForSelector('#loginForm');
      await ap.waitForTimeout(1200);
      let file = path.join(config.out, `admin-login-${width}.png`);
      await adminShot(ap, width, file);
      written.push(file);
      console.log(`png  ${file}`);
      await ap.fill('#loginUser', config.username || 'admin');
      await ap.fill('#loginPass', password);
      await Promise.all([ap.waitForSelector('#appShell:not(.hidden)'), ap.click('#loginForm button[type=submit]')]);
      for (const [name, pageId] of config.admin) {
        // navigate() (admin.html) swaps in a loading placeholder synchronously, so the wait below sees the new page
        await ap.evaluate(id => navigate(id), pageId);
        await ap.waitForFunction(() => {
          const c = document.querySelector('#pageContent');
          return c && c.querySelector('.page-head') && !/جارٍ تحميل البيانات/.test(c.textContent);
        });
        await ap.waitForLoadState('networkidle');
        await sleep(900);
        await ap.evaluate(() => Promise.all(document.getAnimations()
          .filter(a => Number.isFinite(a.effect?.getComputedTiming().endTime)).map(a => a.finished.catch(() => {}))));
        file = path.join(config.out, `admin-${name}-${width}.png`);
        await adminShot(ap, width, file);
        written.push(file);
        console.log(`png  ${file}`);
      }
      await admin.close();
    }
  } finally {
    await browser.close();
  }
  if (problems.length) {
    console.error('Browser errors while taking screenshots:\n  ' + problems.join('\n  '));
    process.exitCode = 1;
  }
  return written;
}

(async () => {
  const [mode, file] = process.argv.slice(2);
  if (!['pdf', 'screens'].includes(mode) || !file) {
    console.error('usage: node tools/render_pdf.cjs pdf JOBS.json | screens CONFIG.json');
    process.exit(2);
  }
  const data = JSON.parse(fs.readFileSync(file, 'utf8'));
  if (mode === 'pdf') await renderPdfs(data);
  else await renderScreens(data);
})().catch(err => { console.error(err && err.stack || err); process.exit(1); });
