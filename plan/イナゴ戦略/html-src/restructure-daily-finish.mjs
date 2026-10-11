import {readFile,writeFile,mkdir,copyFile} from 'node:fs/promises';
import {loadDocument,serializeDocument,base,htmlName} from './document-source.mjs';
async function edit(name,fn){const p=new URL(name,import.meta.url);await writeFile(p,fn(await readFile(p,"utf8")));}
const {readableChapter28}=await import('./readable-chapter.mjs');
const d=await loadDocument();d.version='1.3';d.updated='2026-10-03';
d.source=d.source.replace(/^## 28\. [\s\S]*$/m,readableChapter28())
 .replaceAll('版：1.2','版：1.3').replace('現在版1.2は2026-10-03', '現在版1.3は2026-10-03')
 .replaceAll('F02手順2', '日次全体図の手順2')
 .replaceAll('F02の手順2', '日次全体図の手順2')
 .replace('夜の報告受付と翌日の日次での管理反映を分けたもの。', '夜の報告受付と翌日の日次での管理反映を分け、日次の全体図とサブフローの対応を示したもの。');
const traceP=new URL('要件追跡表.md',base);let trace=await readFile(traceP,'utf8');
trace=trace.replace('（版1.2）','（版1.3）');
trace=trace.replace('\n旧回答と検討資料', '\n| U-20261003-08 | 本人は、日次全体図の後に対応する日本語名のサブフローを置き、日次外のフローを分け、動作テストを初期非表示にするよう依頼した。 | 文書編集者は、28.3.1に6手順の全体図と対応表を置き、28.3.2～6で各詳細を示す。動作テストの図は必要な時だけ開く参考欄へ収録する。 |\n\n旧回答と検討資料').replace('|\n\n| U-20261003-08','|\n| U-20261003-08');
await writeFile(traceP,trace);d.references['要件追跡表.md']=Buffer.from(trace).toString('base64');
// Current dictionary remains substantively unchanged; reference banner tracks its own version.
await edit('finalize-html.mjs',s=>s.replace('辞書・追跡表は1.2へ更新済みです。','辞書は1.2、追跡表は1.3へ更新済みです。')
 .replace("${current ? ' — 1.2更新済み' : ' — 経緯・根拠'}", "${current ? (name === '要件追跡表.md' ? ' — 1.3更新済み' : ' — 1.2更新済み') : ' — 経緯・根拠'}"));
const p=new URL(htmlName,base);await writeFile(p,(await readFile(p,'utf8')).replace(/(<script id="requirements-source" type="application\/json">)[\s\S]*?(<\/script>)/,(_,a,b)=>a+serializeDocument(d)+b));
console.log('Daily overview, five mapped subflows and independent/reference flows arranged.');
