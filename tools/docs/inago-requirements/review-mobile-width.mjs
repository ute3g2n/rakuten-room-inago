import { chromium } from 'playwright-core';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { mkdir, writeFile } from 'node:fs/promises';
import { base, htmlName, reviewMobile } from './document-source.mjs';

const target = fileURLToPath(new URL(htmlName, base));
const output = reviewMobile;
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const results = [];
try {
  for (const width of [320, 360, 390, 430, 768, 820, 900, 1024, 1100]) {
    const page = await browser.newPage({ viewport: { width, height: 900 }, deviceScaleFactor: 1 });
    await page.goto(pathToFileURL(target).href);
    await page.evaluate(() => document.fonts.ready);
    const initiallyOpenFlows = await page.locator('.flow-transcript[open]').count();
    if (width <= 800 && initiallyOpenFlows !== 11) throw new Error(`Task flow descriptions should be open on phones: ${initiallyOpenFlows}`);
    await page.locator('details').evaluateAll(details => details.forEach(details => { details.open = true; }));
    const result = await page.evaluate(() => {
      const all = [...document.querySelectorAll('body *')];
      const overflows = all.filter(el => el.scrollWidth > el.clientWidth + 1
        && ['auto', 'scroll'].includes(getComputedStyle(el).overflowX))
        .map(el => ({ tag: el.tagName, className: String(el.className?.baseVal ?? el.className ?? ''), extra: el.scrollWidth - el.clientWidth,
          overflowX: getComputedStyle(el).overflowX, childWidth: el.querySelector('svg,table')?.getBoundingClientRect().width,
          childMinWidth: el.querySelector('svg,table') ? getComputedStyle(el.querySelector('svg,table')).minWidth : null }))
        .slice(0, 12);
      const figures = [...document.querySelectorAll('figure')].map(el => {
        const box = el.getBoundingClientRect();
        const svg = el.querySelector('svg');
        const svgBox = svg.getBoundingClientRect();
        return { width: box.width, right: box.right, svgWidth: svgBox.width,
          viewBox: svg.viewBox.baseVal.width, ratio: svgBox.width / svg.viewBox.baseVal.width,
          svgHeightRatio: svgBox.height / svg.viewBox.baseVal.height };
      });
      const tables = [...document.querySelectorAll('.table-scroll')].map(el => ({ client: el.clientWidth, scroll: el.scrollWidth }));
      const unlabeledCells = [...document.querySelectorAll('.md td')].filter(el => !el.dataset.label).length;
      const distortedFigures = figures.filter(el => Math.abs(el.ratio - el.svgHeightRatio) > 0.001).length;
      return { viewport: innerWidth, document: document.documentElement.scrollWidth, body: document.body.scrollWidth,
        figures: figures.length, maxFigureRight: Math.max(...figures.map(x => x.right)), smallestSvgScale: Math.min(...figures.map(x => x.ratio)),
        tables: tables.length, maxTableOverflow: Math.max(0, ...tables.map(x => x.scroll - x.client)),
        unlabeledCells, distortedFigures, wideScrollableElements: overflows,
        mobileTableSample: document.querySelector('#source-26 .table-scroll')?.innerText.slice(0, 140) };
    });
    if (result.document > width || result.body > width || result.maxFigureRight > width + 1 || result.wideScrollableElements.length || result.maxTableOverflow || result.unlabeledCells || result.distortedFigures) {
      throw new Error(`Mobile layout exceeds ${width}px: ${JSON.stringify(result)}`);
    }
    if (width === 390) {
      const flow = page.locator('#task-F02');
      await flow.locator('svg').screenshot({ path: fileURLToPath(new URL('図_390px.png', output)) });
      await page.locator('#source-26 .table-scroll tbody tr').first().screenshot({ path: fileURLToPath(new URL('表_390px.png', output)) });
    }
    results.push(result);
    await page.close();
  }
  await writeFile(new URL('モバイル表示検証.json', output), JSON.stringify({ status: 'PASS', checkedAt: new Date().toISOString(), results }, null, 2) + '\n');
  console.log(JSON.stringify({ status: 'PASS', results }, null, 2));
} finally {
  await browser.close();
}
