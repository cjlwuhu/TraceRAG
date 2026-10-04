// Render the SVG locally with the project's Playwright and installed Edge.
const path = require('node:path');
const fs = require('node:fs');
const { chromium } = require(path.resolve(__dirname, '../../web/node_modules/@playwright/test'));

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true, args: ['--disable-gpu'] });
  try {
    const page = await browser.newPage({ viewport: { width: 1640, height: 2230 }, deviceScaleFactor: 1 });
    const svg = fs.readFileSync(path.join(__dirname, 'rca-rag-workflow.svg'), 'utf8');
    await page.setContent('<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;background:white}svg{display:block}</style></head><body>' + svg + '</body></html>');
    await page.screenshot({ path: path.join(__dirname, 'rca-rag-workflow.png'), fullPage: true, timeout: 20000 });
    console.log('Rendered 1640 × 2230 PNG preview.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
