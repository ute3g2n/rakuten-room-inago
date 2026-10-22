// Diagnostic wrapper: run the production adapter and preserve public UI evidence.
import { chromium } from 'playwright-core';
import { writeFile } from 'node:fs/promises';
import { main } from '../../../../automation/inago/adapters/public_sources.mjs';

let raw = '';
for await (const chunk of process.stdin) raw += chunk;
const { snapshot_path: snapshotPath, ...input } = JSON.parse(raw);
const originalLaunch = chromium.launchPersistentContext.bind(chromium);
chromium.launchPersistentContext = async (...args) => {
  const context = await originalLaunch(...args);
  const close = context.close.bind(context);
  context.close = async (...closeArgs) => {
    try {
      const pages = [];
      for (const page of context.pages()) {
        if (!/^https:\/\/trends\.google\.(co\.jp|com)\//.test(page.url())) continue;
        pages.push(await page.evaluate(() => ({
          url: location.href,
          inputs: Array.from(document.querySelectorAll('input')).map(element => ({
            role: element.getAttribute('role'),
            label: element.getAttribute('aria-label'),
            value: element.value,
            disabled: element.disabled,
            visible: Boolean(element.getClientRects().length),
          })),
          widgets: Array.from(document.querySelectorAll('.fe-related-queries')).map(element => ({
            text: element.innerText.slice(0, 12000),
            dropdowns: Array.from(element.querySelectorAll('md-select')).map(select => ({
              label: select.getAttribute('aria-label'),
              class: select.className,
              text: select.innerText,
              visible: Boolean(select.getClientRects().length),
            })),
            rowCount: element.querySelectorAll('a.progress-label').length,
          })),
          tables: Array.from(document.querySelectorAll('table')).map(element => ({
            label: element.getAttribute('aria-label'),
            text: element.innerText.slice(0, 12000),
            visible: Boolean(element.getClientRects().length),
          })),
          migrationLinks: Array.from(document.querySelectorAll('a')).filter(element =>
            /新しい.*探す|従来.*探す/.test(element.innerText)
          ).map(element => ({text: element.innerText, href: element.href})),
        })));
      }
      await writeFile(snapshotPath, JSON.stringify({pages}, null, 2), 'utf8');
    } catch (error) {
      process.stderr.write(JSON.stringify({event: 'diagnostic_capture_failed', error_class: error.name}) + '\n');
    }
    return await close(...closeArgs);
  };
  return context;
};

try {
  const result = await main(input);
  process.stdout.write(JSON.stringify(result) + '\n');
} catch (error) {
  process.stderr.write(JSON.stringify({event: 'diagnostic_failure', error_class: error.name, message: error.message.slice(0, 1600)}) + '\n');
  process.stdout.write(JSON.stringify({error_code: error.code ?? (error.name === 'TimeoutError' ? 'source_timeout' : 'browser_adapter_error')}) + '\n');
  process.exitCode = 1;
}
