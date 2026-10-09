import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import puppeteer from '../.cache/npm/_npx/702923228c2ce1e6/node_modules/puppeteer-core/lib/puppeteer/puppeteer-core.js';
process.chdir(fileURLToPath(new URL('.', import.meta.url)));

// Keep the teaching example internally consistent.
const weights = [.6, .3, .1];
const matrix = [[1, 2, 6], [.5, 1, 3], [1 / 6, 1 / 3, 1]];
assert(Math.abs(weights.reduce((a, b) => a + b) - 1) < 1e-12);
matrix.forEach((row, i) => row.forEach((v, j) => assert(Math.abs(v - weights[i] / weights[j]) < 1e-12)));
assert(Math.abs(weights.reduce((sum, w, i) => sum + w * [.7, .4, .5][i], 0) - .59) < 1e-12);

const browser = await puppeteer.launch({
  executablePath: 'C:/Users/15827/AppData/Local/ms-playwright/chromium-1223/chrome-win64/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-dev-shm-usage'],
});
try {
  const page = await browser.newPage();
  await page.setViewport({ width: 1920, height: 1080, deviceScaleFactor: 1 });
  await page.goto('http://localhost:5173/ahp-tutorial/index.html', { waitUntil: 'networkidle0' });
  assert.equal(await page.$$eval('[data-composition-id="ahp"]', els => els.length), 1, 'Video composition must load');
  await page.evaluate(() => document.fonts.ready);
  await mkdir('qa', { recursive: true });
  const animated = process.argv.includes('--animated');
  const times = animated ? [1.9, 3.82, 5.9, 7.35, 9.9, 11.45, 13.9, 14.9] : [1, 2, 3, 4];
  const results = [];
  for (const time of times) {
    await page.evaluate(({ time, animated }) => {
      if (animated) window.__timelines.ahp.seek(time);
      else document.querySelectorAll('.scene').forEach((el, i) => el.style.opacity = i + 1 === time ? 1 : 0);
    }, { time, animated });
    const file = `qa/${animated ? 'time' : 'layout'}-${time}.png`;
    await page.screenshot({ path: file });
    const overflow = await page.evaluate(() => [...document.querySelectorAll('.scene *')].filter(el => {
      const scene = el.closest('.scene');
      return getComputedStyle(scene).opacity === '1' && el.children.length === 0 && el.textContent.trim() && (el.scrollWidth > el.clientWidth + 2 || el.scrollHeight > el.clientHeight + 2);
    }).map(el => ({ text: el.textContent, selector: el.className, scroll: [el.scrollWidth, el.scrollHeight], client: [el.clientWidth, el.clientHeight] })));
    results.push({ time, file, overflow });
  }
  await writeFile('qa/check.json', JSON.stringify(results, null, 2));
  console.log(JSON.stringify(results));
} finally { await browser.close(); }
