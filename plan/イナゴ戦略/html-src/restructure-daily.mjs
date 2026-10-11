import {readFile,writeFile,mkdir,copyFile} from 'node:fs/promises';
import {loadDocument,serializeDocument,base,htmlName} from './document-source.mjs';
await mkdir(new URL('html-review-1.3/',base),{recursive:true});
await copyFile(new URL(htmlName,base),new URL('html-review-1.3/構成変更前のHTML.html',base));
async function edit(name,fn){const p=new URL(name,import.meta.url);await writeFile(p,fn(await readFile(p,'utf8')));}
await edit('flow-descriptions.mjs',s=>s
 .replace(/  F02: \[[\s\S]*?\n  \],/,`  F02: [
    n('S','S-TRIGGER／ORCH-S／S-REPO','日次制御プログラムと保存担当プログラム','前日の投稿結果を今日の商品選びへ反映する','前日の投稿済み・見送り・訂正と、過去の未反映報告','13:00の開始時に回収し、管理情報と反映済みの印を同時に確定します','詳しい手順は「日次処理を開始し、前日の投稿報告を管理情報へ反映する」です。反映が未完了なら商品探索を開始しません'),
    n('S','S-SELECTION／S-TRENDS／AG-KEY','テーマ選択・人気語取得プログラムと検索語判断AI','偏りを抑えながら当日の人気語で商品を探す','選択回数の少ない3テーマと、実際に取得した関連人気語','選び、子分類だけを残して第1・第2階層の検索語を抽選し、結果を保存します','詳しい手順は「本日の検索語と候補商品を選び、不足分を探す」の手順1～4です。取得失敗では当日の語句選びを停止します'),
    n('S','S-ROOM／S-SELECTION','商品検索・選択プログラム','紹介文を作る商品を重複なく集める','第1階層と第2階層を組み合わせた検索語','使ってROOMを検索し、いいね300以上の投稿を商品単位にまとめ、投稿済み・予約中の商品を除いて候補を選びます','詳しい手順は「本日の検索語と候補商品を選び、不足分を探す」の手順5～7です。1検索は内部順の最大50投稿、再試行を含め1日9検索までです'),
    n('S','AG-COPY／S-AI-RUNNER／S-REPO','本文作成AIと保存担当プログラム','本人が貼り付けて使える紹介文を1日15件用意する','選ばれた各商品の情報と、採用済みの文章の型','読み、日本語の本文を1案ずつ作り、本文・予約・完成件数を商品ごとに確定保存します','詳しい手順は「商品の紹介文を生成し、確定保存する」です。15件に不足し検索枠が残れば手順3へ戻り、別の語が必要なら手順2へ戻ります'),
    n('S','ORCH-S／S-METRICS／S-REPO','日次制御・成果取得・保存プログラム','本文作成の後に実際の売上と投稿反応を記録する','楽天アフィリエイトの成果と、自分のROOM投稿への反応','語句更新・検索・本文生成・保存が終わった直後に取得し、取得日時と範囲を分けて保存します','詳しい手順は「売上と投稿への反応を取得し、保存する」です。17:45まで待たず、前工程が長引いた時だけ17:45で打ち切ります。成果取得は18:00が上限です'),
    n('S','S-EXPORT／S-BACKUP／S-RESTORE','表示・バックアップ・復元確認プログラム','本人へ作成結果を渡し、保存情報の消失に備える','保存済み本文、成果、不足理由、管理情報、未反映の受付記録','HTMLへ出力し、日次バックアップを作り、当月の復元確認が未実施の場合は別の検査領域で確認します','保全の詳しい手順は「結果を保全し、バックアップと復元確認を行う」です。日次は本人の夜の投稿を待たず終了し、その報告は翌日の手順1が回収します'),
  ],`)
 .replace(/    n\('A','AG-INSIGHT','成果の説明を担当するAI','本人が結果の意味と限界を理解できるようにする',[^\n]+\n/,'')
 .replaceAll('F02の手順2', '日次フロー全体図の手順2')
 .replaceAll('独立したF05', '独立した「本人が夜に投稿し、投稿済み・見送り・訂正を報告する」フロー')
 .replaceAll('翌日の日次処理のF00手順4～6', '翌日の「日次処理を開始し、前日の投稿報告を管理情報へ反映する」フローの手順4～6')
 .replaceAll('翌日のF00', '翌日の開始・投稿報告反映フロー')
 .replaceAll('F02の手順5で作り手順6で保存します', '日次全体の手順4で作成・保存します')
 .replaceAll('本文作成AIが返した本文を保存担当プログラムへ渡し、保存が成功した商品だけを完成として数えます', '選出した商品を日次全体の手順4の本文生成・保存フローへ渡し、保存が成功した商品だけを完成として数えます')
 .replace("  F02: [", "  F02: [")
 .replace(/^  F02: \['日次制御プログラムは、通常の入口[^\n]+$/m, "  F02: ['日次制御プログラムは、手順4で15件に届かず探索枠が残る場合、手順3へ戻ります。別の語句が必要なら手順2の語句選びへ戻ります。', '日次制御プログラムは、15件達成・9検索到達・候補なし・取得不能で前工程が終わると、直ちに手順5の成果取得へ進みます。17:45は長時間化した前工程だけの打切り上限です。', '本人は21:30などに独立して投稿します。その報告を翌日の日次の手順1が回収し、自動処理は本人投稿を待ちません。'],")
 .replace(/^  F06: .*$/m, "  F06: ['成果取得プログラムは、通常は前工程終了直後に取得を開始し、取得終了または18:00で日次全体の手順6へ進みます。', '成果説明AIによる解釈は、本人が必要な時に依頼する別のフローです。毎日の成果取得には含めません。'],"));
await edit('flow-renderer.mjs',s=>s
 .replace('`${id}　${flow.title}`','flow.title')
 .replace(/  if \(id === 'F02'\) \{[\s\S]*?\n  \}\n  const geometry/, '  const geometry')
 .replace("${id === 'F02' && index === 0 ? ' stroke-dasharray=\"7 5\"' : ''}", '')
 .replace("id === 'F06' && index === 4", 'false')
 .replace(/^  if \(id === 'F02'\) content[^\n]+$/m, "  if (id === 'F02') content += arrow(id, `M 1070 ${geometry[3].middle} H 1134 V ${geometry[2].middle} H 1074`, true);")
 .replaceAll('F02の手順2', '日次フロー全体図の手順2'));
await edit('build.mjs',s=>s
 .replace("https?:\\/\\/|[^:/?#]+", "https?:\\/\\/|#[A-Za-z0-9_-]+|[^:/?#]+")
 .replace("      html.push(`<figure id=\"task-${flow.id}\"", "      const start = flow.id === 'F11' ? '<details class=\"implementation-reference\" id=\"implementation-test-flow\"><summary data-ui-only>参考：動作テストの図を開く</summary>' : '';\n      const end = flow.id === 'F11' ? '</details>' : '';\n      html.push(`${start}<figure id=\"task-${flow.id}\"")
 .replace('${renderFlowTranscript(flow)}</figure>`);', '${renderFlowTranscript(flow)}</figure>${end}`);')
 .replace('現在版1.2は追加依頼・時刻訂正・説明改訂・事前分析の対象外化を統合したもの。', '現在版1.3は最新の本人指示を統合し、日次全体図と対応するサブフローへ読み順を整理したもの。')
 .replaceAll('U-20261003-01〜07','U-20261003-01〜08')
 .replaceAll('HTML正本1.2','HTML正本1.3').replaceAll('版1.2へ統合','版1.3へ統合')
 .replaceAll('HTML正本 1.2 · 2026.10.03','HTML正本 1.3 · 2026.10.03'));
await edit('interactions.js',s=>s
 .replace('.glossary-group, .flow-transcript', '.glossary-group, .flow-transcript, .implementation-reference')
 .replace("chapter.querySelectorAll('.glossary-group')", "chapter.querySelectorAll('.glossary-group, .implementation-reference')")
 .replace("    if (target.tagName === 'DETAILS') target.open = true;", "    let parent = target;\n    while (parent) { if (parent.tagName === 'DETAILS') parent.open = true; parent = parent.parentElement; }"));
await edit('reference-styles.css',s=>s+'\n.implementation-reference{margin:20px 0;padding:16px;border:1px solid #c7d9d0;border-radius:9px;background:#f7faf8}.implementation-reference>summary{font-weight:700;cursor:pointer;color:#173b3a}\n');
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
