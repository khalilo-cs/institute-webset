// Regenerates every PNG icon from the brand glyph (the curtain mark used in the site header).
//   NODE_PATH=$(npm root -g) node tools/make_icons.cjs
// Needs the `playwright` package with a Chromium build; nothing here is used at runtime.
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const ROOT = path.resolve(__dirname, '..');
const OUT = path.join(ROOT, 'icons');
const STORE = path.join(ROOT, 'android', 'store-assets');
fs.mkdirSync(OUT, { recursive: true });
fs.mkdirSync(STORE, { recursive: true });

const THEMES = {
  customer: { bg: '#596654', fg: '#f8f4ec', accent: '#b99b70', prefix: '' },
  admin: { bg: '#292d26', fg: '#e9d9b8', accent: '#b99969', prefix: 'admin-' },
};

// Same path data as the <svg> logo in index.html / admin.html (24x24 grid).
const GLYPH = '<path d="M4 18V6l8 6 8-6v12" /><path d="M8 18v-5m8 5v-5" />';

function svg({ bg, fg, accent }, { size, rounded, scale, admin }) {
  const radius = rounded ? size * 0.22 : 0;
  const glyph = size * scale;
  const offset = (size - glyph) / 2;
  const badge = admin
    ? `<g transform="translate(${size * 0.64} ${size * 0.64})"><circle cx="${size * 0.14}" cy="${size * 0.14}" r="${size * 0.14}" fill="${accent}"/>` +
      [[0.075, 0.075], [0.145, 0.075], [0.075, 0.145], [0.145, 0.145]]
        .map(([x, y]) => `<rect x="${size * x - size * 0.0025}" y="${size * y - size * 0.0025}" width="${size * 0.056}" height="${size * 0.056}" rx="${size * 0.01}" fill="${bg}"/>`).join('') + '</g>'
    : '';
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
    <rect width="${size}" height="${size}" rx="${radius}" fill="${bg}"/>
    <g transform="translate(${offset} ${admin ? offset - size * 0.02 : offset}) scale(${glyph / 24})" fill="none" stroke="${fg}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${GLYPH}</g>
    ${badge}
  </svg>`;
}

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  async function render(markup, size, file, transparent) {
    await page.setViewportSize({ width: size, height: size });
    await page.setContent(`<html><body style="margin:0;background:transparent">${markup}</body></html>`);
    const buf = await page.screenshot({ omitBackground: transparent, clip: { x: 0, y: 0, width: size, height: size } });
    fs.writeFileSync(file, buf);
    console.log('wrote', path.relative(ROOT, file));
  }
  for (const [name, theme] of Object.entries(THEMES)) {
    const admin = name === 'admin';
    const p = theme.prefix;
    // "any" icons keep their rounded corners; the glyph fills ~58 % of the tile.
    await render(svg(theme, { size: 192, rounded: true, scale: 0.58, admin }), 192, path.join(OUT, `${p}icon-192.png`), true);
    await render(svg(theme, { size: 512, rounded: true, scale: 0.58, admin }), 512, path.join(OUT, `${p}icon-512.png`), true);
    // "maskable" icons are full-bleed; the glyph stays inside the central 80 % safe zone.
    await render(svg(theme, { size: 512, rounded: false, scale: 0.46, admin }), 512, path.join(OUT, `${p}maskable-512.png`), false);
    await render(svg(theme, { size: 180, rounded: false, scale: 0.5, admin }), 180, path.join(OUT, `${p}apple-touch-icon.png`), false);
    // Google Play listing icon (512 px, full square, Play applies its own mask).
    await render(svg(theme, { size: 512, rounded: false, scale: 0.5, admin }), 512, path.join(STORE, `${name}-play-icon-512.png`), false);
  }
  await render(svg(THEMES.customer, { size: 48, rounded: true, scale: 0.62, admin: false }), 48, path.join(OUT, 'favicon-48.png'), true);
  await browser.close();
})();
