import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import {
  buildResearchBrief,
  currentJstDate,
  parseCompletedPosts,
  parseProductLedger,
  renderMarkdownBrief,
} from './research-planner-lib.mjs';

const root = fileURLToPath(new URL('../', import.meta.url));

function parseArgs(args) {
  const options = { date: currentJstDate(), format: 'md', help: false, jsonOutput: null };
  for (let index = 0; index < args.length; index += 1) {
    const [flag, inlineValue] = args[index].split('=', 2);
    if (flag === '--help' || flag === '-h') options.help = true;
    else if (flag === '--date') options.date = inlineValue ?? args[++index];
    else if (flag === '--format') options.format = inlineValue ?? args[++index];
    else if (flag === '--json-output') options.jsonOutput = inlineValue ?? args[++index];
    else throw new Error(`不明な引数です: ${args[index]}`);
  }
  if (!['md', 'json'].includes(options.format)) throw new Error('--format は md または json を指定してください');
  if (!options.date) throw new Error('--date の値がありません');
  if (options.jsonOutput === undefined) throw new Error('--json-output の値がありません');
  return options;
}

async function readJson(relativePath) {
  return JSON.parse(await readFile(path.join(root, relativePath), 'utf8'));
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  if (options.help) {
    process.stdout.write('Usage: npm run research:brief -- [--date YYYY-MM-DD] [--format md|json] [--json-output PATH]\n');
    return;
  }
  const [productsText, postsText, plan, genreMap] = await Promise.all([
    readFile(path.join(root, 'rakuten-room/products.md'), 'utf8'),
    readFile(path.join(root, 'rakuten-room/posts-log.md'), 'utf8'),
    readJson('rakuten-room-kai/research/genre-plan.json'),
    readJson('rakuten-room-kai/research/genre-map.json'),
  ]);
  const brief = buildResearchBrief({
    products: parseProductLedger(productsText),
    posts: parseCompletedPosts(postsText),
    plan,
    genreMap,
    asOf: options.date,
  });
  if (options.jsonOutput) {
    const target = path.resolve(options.jsonOutput);
    await mkdir(path.dirname(target), { recursive: true });
    await writeFile(target, `${JSON.stringify(brief, null, 2)}\n`, 'utf8');
  }
  process.stdout.write(options.format === 'json'
    ? `${JSON.stringify(brief, null, 2)}\n`
    : renderMarkdownBrief(brief));
}

main().catch((error) => {
  process.stderr.write(`research:brief: ${error.message}\n`);
  process.exitCode = 1;
});
