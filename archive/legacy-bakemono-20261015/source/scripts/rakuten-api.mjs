import { callRakutenApi } from './rakuten-api-lib.mjs';
import { loadRakutenCredentials } from './rakuten-credentials.mjs';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';

const FLAGS = new Map([
  ['--keyword', 'keyword'],
  ['--genre-id', 'genreId'],
  ['--item-code', 'itemCode'],
  ['--shop-code', 'shopCode'],
  ['--hits', 'hits'],
  ['--page', 'page'],
  ['--sort', 'sort'],
  ['--period', 'period'],
  ['--format', 'format'],
  ['--json-output', 'jsonOutput'],
]);

function parseArgs(args) {
  const [command, ...rest] = args;
  if (!['search', 'ranking', 'verify'].includes(command)) {
    throw new Error('search・ranking・verify のいずれかを指定してください');
  }
  const options = { format: 'brief' };
  for (let index = 0; index < rest.length; index += 1) {
    const [flag, inlineValue] = rest[index].split('=', 2);
    if (flag === '--help' || flag === '-h') return { command, help: true };
    const key = FLAGS.get(flag);
    if (!key) throw new Error(`不明な引数です: ${flag}`);
    const value = inlineValue ?? rest[++index];
    if (value === undefined || value === '') throw new Error(`${flag} の値がありません`);
    options[key] = ['hits', 'page'].includes(key) ? Number(value) : value;
  }
  if (!['brief', 'json'].includes(options.format)) throw new Error('--format は brief または json を指定してください');
  if (command === 'ranking' && ['keyword', 'itemCode', 'shopCode', 'hits', 'sort'].some((key) => options[key] !== undefined)) {
    throw new Error('ランキングでは keyword・itemCode・shopCode・hits・sort は使えません');
  }
  if (command === 'search' && options.period !== undefined) throw new Error('検索では period は使えません');
  if (command === 'verify' && ['itemCode', 'shopCode', 'hits', 'page', 'sort', 'period'].some((key) => options[key] !== undefined)) {
    throw new Error('verify では keyword・genre-id・format のみ指定できます');
  }
  return { command, options, help: false };
}

function clean(value) {
  return String(value ?? '')
    .replace(/[\u0000-\u001f\u007f]/g, ' ')
    .replace(/\|/g, '｜')
    .replace(/\s+/g, ' ')
    .trim();
}

function briefOutput(result, limit = 10) {
  const header = result.kind === 'search'
    ? `検索: ${result.count ?? '?'}件 / ページ ${result.page ?? '?'} / 取得 ${result.items.length}件`
    : `ランキング: ${clean(result.title) || '総合'} / 更新 ${clean(result.lastBuildDate) || '不明'} / 取得 ${result.items.length}件`;
  const lines = [header];
  for (const item of result.items.slice(0, limit)) {
    const rank = item.rank == null ? '' : `#${item.rank} `;
    const price = item.itemPrice == null ? '価格不明' : `${item.itemPrice}円`;
    const reviews = item.reviewCount == null ? 'レビュー不明' : `レビュー${item.reviewCount}件`;
    lines.push(`${rank}${clean(item.itemCode)} | ${clean(item.itemName)} | ${price} | ${reviews} | ${clean(item.itemUrl)}`);
  }
  return `${lines.join('\n')}\n`;
}

async function main() {
  const { command, options, help } = parseArgs(process.argv.slice(2));
  if (help) {
    process.stdout.write('search: --keyword TEXT | --genre-id ID | --item-code CODE | --shop-code CODE [--hits 1..30] [--page 1..100] [--sort SORT] [--format brief|json] [--json-output PATH]\nranking: [--genre-id ID] [--page 1..34] [--period realtime] [--format brief|json] [--json-output PATH]\nverify: [--keyword TEXT] [--genre-id ID] [--format brief|json] [--json-output PATH]\n');
    return;
  }
  const credentials = await loadRakutenCredentials();
  const { jsonOutput, ...requestOptions } = options;

  if (command === 'verify') {
    const search = await callRakutenApi('search', { keyword: requestOptions.keyword ?? 'スープ', hits: 1 }, credentials);
    if (options.format === 'brief') process.stdout.write(briefOutput(search, 1));
    await new Promise((resolve) => setTimeout(resolve, 1200));
    const ranking = await callRakutenApi('ranking', { genreId: requestOptions.genreId, page: 1 }, credentials);
    if (jsonOutput) {
      const target = path.resolve(jsonOutput);
      await mkdir(path.dirname(target), { recursive: true });
      await writeFile(target, `${JSON.stringify({ search, ranking }, null, 2)}\n`, 'utf8');
    }
    if (options.format === 'brief') process.stdout.write(briefOutput(ranking, 1));
    else process.stdout.write(`${JSON.stringify({ search: { ...search, items: search.items.slice(0, 1) }, ranking: { ...ranking, items: ranking.items.slice(0, 1) } }, null, 2)}\n`);
    return;
  }

  const result = await callRakutenApi(command, requestOptions, credentials);
  if (jsonOutput) {
    const target = path.resolve(jsonOutput);
    await mkdir(path.dirname(target), { recursive: true });
    await writeFile(target, `${JSON.stringify(result, null, 2)}\n`, 'utf8');
  }
  process.stdout.write(options.format === 'json'
    ? `${JSON.stringify(result, null, 2)}\n`
    : briefOutput(result));
}

main().catch((error) => {
  process.stderr.write(`rakuten-api: ${error.message}\n`);
  process.exitCode = 1;
});
