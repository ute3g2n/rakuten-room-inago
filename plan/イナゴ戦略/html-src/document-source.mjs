import { readFile, readdir } from 'node:fs/promises';

export const base = new URL('../', import.meta.url);
export const htmlName = 'イナゴスキル要件定義書.html';
export const legacyName = 'イナゴスキル要件定義書.md';
export const sourceId = 'requirements-source';

export async function loadDocument({ importMarkdown = false } = {}) {
  if (!importMarkdown) {
    const html = await readFile(new URL(htmlName, base), 'utf8');
    const encoded = html.match(/<script id="requirements-source" type="application\/json">([\s\S]*?)<\/script>/)?.[1];
    if (!encoded) throw new Error('Canonical HTML has no source snapshot. Use --import-markdown once for migration.');
    const data = JSON.parse(encoded);
    if (data.formatVersion !== 1 || !/^\d+\.\d+(?:\.\d+)?$/.test(data.version) || typeof data.source !== 'string' || !data.references) throw new Error('Invalid canonical source snapshot');
    return data;
  }
  const source = (await readFile(new URL(legacyName, base), 'utf8')).replace(/\r\n/g, '\n');
  const references = {};
  const files = await readdir(base);
  for (const name of files.filter(name => name.endsWith('.md') && name !== legacyName && !name.startsWith('HTMLドキュメントレビュー'))) {
    references[name] = (await readFile(new URL(name, base))).toString('base64');
  }
  return { formatVersion: 1, version: '1.1', updated: '2026-10-03', source, references };
}

export const serializeDocument = data => JSON.stringify(data).replace(/</g, '\\u003c').replace(/>/g, '\\u003e').replace(/&/g, '\\u0026');
