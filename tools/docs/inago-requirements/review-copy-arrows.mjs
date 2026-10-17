import { chromium } from 'playwright-core';
import { readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';
import assert from 'node:assert/strict';
import { archiveReviewArrowFix, base, htmlName, reviewArrowFix } from './document-source.mjs';

const evidence = reviewArrowFix;
const target = process.argv[2] || fileURLToPath(new URL(htmlName, base));
const before = await readFile(new URL('修正前のHTML.html', archiveReviewArrowFix), 'utf8');
const after = await readFile(target, 'utf8');
const source = html => html.match(/<script id="requirements-source" type="application\/json">([\s\S]*?)<\/script>/)[1];
assert.equal(source(after), source(before), '要件本文・根拠資料は変更しない');
const figures = html => [...html.matchAll(/<figure id="task-(F\d+)"[\s\S]*?<\/figure>/g)];
const oldFigures = new Map(figures(before).map(m => [m[1], m[0]]));
for (const m of figures(after)) if (m[1] !== 'F04') assert.equal(m[0], oldFigures.get(m[1]), m[1] + 'は変更しない');

const browser = await chromium.launch({ channel: 'msedge', headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  await page.goto(pathToFileURL(target).href + '#task-F04');
  await page.evaluate(() => document.fonts.ready);
  const geometry = await page.locator('#task-F04 svg').evaluate(svg => {
    const nodes = [...svg.querySelectorAll('.diagram-node > rect')].map(r => ({
      x: r.x.baseVal.value, y: r.y.baseVal.value, w: r.width.baseVal.value, h: r.height.baseVal.value,
    }));
    const onBorder = (p, r) => {
      const near = (a, b) => Math.abs(a - b) < 0.01;
      return ((near(p.x, r.x) || near(p.x, r.x + r.w)) && p.y >= r.y && p.y <= r.y + r.h)
        || ((near(p.y, r.y) || near(p.y, r.y + r.h)) && p.x >= r.x && p.x <= r.x + r.w);
    };
    return [...svg.querySelectorAll('path[marker-end]')].map(path => {
      const start = path.getPointAtLength(0), end = path.getPointAtLength(path.getTotalLength());
      return {
        from: nodes.findIndex(r => onBorder(start, r)) + 1,
        to: nodes.findIndex(r => onBorder(end, r)) + 1,
        conditional: path.hasAttribute('stroke-dasharray'),
      };
    });
  });
  assert.deepEqual(geometry, [
    { from: 1, to: 2, conditional: false }, { from: 2, to: 3, conditional: false },
    { from: 3, to: 4, conditional: false }, { from: 4, to: 5, conditional: false },
    { from: 5, to: 6, conditional: false }, { from: 3, to: 7, conditional: true },
    { from: 7, to: 1, conditional: true },
  ], '各矢印の始点と終点が正しいノードの境界に接続する');
  await page.locator('#task-F04').screenshot({ path: fileURLToPath(new URL('紹介文フロー.png', evidence)) });
  await writeFile(new URL('矢印接続検証.json', evidence), JSON.stringify({
    target, result: 'PASS', sourceUnchanged: true, otherFlowsUnchanged: true, connectedArrows: geometry,
  }, null, 2) + '\n');
  console.log(JSON.stringify({ result: 'PASS', target, connectedArrows: geometry.length, sourceUnchanged: true, otherFlowsUnchanged: true }));
} finally {
  await browser.close();
}
