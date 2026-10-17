import { readFile } from 'node:fs/promises';
import { isAbsolute, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

export const repositoryRoot = fileURLToPath(new URL('../../../', import.meta.url));
export const base = new URL('../../../docs/specifications/inago/', import.meta.url);
export const reviewRoot = new URL(
  '../../../worklogs/projects/requirements/reviews/',
  import.meta.url,
);
export const review12 = new URL('html-review-1.2/', reviewRoot);
export const review13 = new URL('html-review-1.3/', reviewRoot);
export const reviewArrowFix = new URL('html-review-arrow-fix/', reviewRoot);
export const reviewMobile = new URL('html-review-mobile/', reviewRoot);
export const notesRoot = new URL(
  '../../../worklogs/projects/requirements/notes/',
  import.meta.url,
);
export const archiveRoot = new URL(
  '../../../archive/documents/requirements/',
  import.meta.url,
);
export const archiveReview13 = new URL('html-review-1.3/', archiveRoot);
export const archiveReviewArrowFix = new URL('html-review-arrow-fix/', archiveRoot);
export const archiveReviewMobile = new URL('html-review-mobile/', archiveRoot);
export const htmlName = 'イナゴスキル要件定義書.html';
export const sourceId = 'requirements-source';

export async function loadDocument() {
  const html = await readFile(new URL(htmlName, base), 'utf8');
  const encoded = html.match(
    /<script id="requirements-source" type="application\/json">([\s\S]*?)<\/script>/,
  )?.[1];
  if (!encoded) throw new Error('Canonical HTML has no source snapshot.');

  const data = JSON.parse(encoded);
  if (
    data.formatVersion !== 1
    || !/^\d+\.\d+(?:\.\d+)?$/.test(data.version)
    || typeof data.source !== 'string'
    || !data.references
    || typeof data.references !== 'object'
  ) {
    throw new Error('Invalid canonical source snapshot');
  }

  const attachmentIds = data.referenceDocumentIds;
  if (!attachmentIds || typeof attachmentIds !== 'object' || Array.isArray(attachmentIds)) {
    throw new Error('Canonical source snapshot has no attachment document-ID map.');
  }
  const index = JSON.parse(
    await readFile(new URL('../../document-index.json', base), 'utf8'),
  );
  const documents = new Map(
    index.documents.map(document => [document.document_id, document]),
  );
  const referenceNames = Object.keys(data.references);
  if (
    referenceNames.length !== Object.keys(attachmentIds).length
    || referenceNames.some(name => !attachmentIds[name])
  ) {
    throw new Error('Attachment names and document-ID map differ.');
  }
  for (const name of referenceNames) {
    const document = documents.get(attachmentIds[name]);
    if (!document || document.visibility !== 'repository' || !document.current_path) {
      throw new Error(`Attachment document is unavailable: ${name}`);
    }
    const parts = document.current_path.replaceAll('\\', '/').split('/');
    if (
      isAbsolute(document.current_path)
      || parts.includes('..')
      || document.current_path.includes('\0')
    ) {
      throw new Error(`Unsafe attachment document path: ${name}`);
    }
    const target = resolve(repositoryRoot, ...parts);
    const fromRoot = relative(repositoryRoot, target);
    if (fromRoot.startsWith('..') || isAbsolute(fromRoot)) {
      throw new Error(`Attachment document escapes repository: ${name}`);
    }
    data.references[name] = (await readFile(target)).toString('base64');
  }
  return data;
}

export async function readReferenceDocument(name, sourceData = null) {
  const data = sourceData ?? await loadDocument();
  const encoded = data.references[name];
  if (!encoded) throw new Error(`Unknown embedded reference document: ${name}`);
  return Buffer.from(encoded, 'base64');
}

export const serializeDocument = data => JSON.stringify(data)
  .replace(/</g, '\\u003c')
  .replace(/>/g, '\\u003e')
  .replace(/&/g, '\\u0026');
