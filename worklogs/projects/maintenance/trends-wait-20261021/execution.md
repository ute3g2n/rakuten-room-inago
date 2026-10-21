# Google Trends：1秒待機試験と現行UIの照合（2026-10-21）

## 結果

**実ブラウザーの新UIで「スキンケア／日本／過去3か月／ウェブ検索」の上位関連語20件を取得できた。** 各操作前に1秒待機した。20件の順位・原文は `new-ui-success.json` に保存。検証タブはヘッドありのユーザーChromeで開いたままにし、本人依頼でそのウィンドウを前面表示した。

新しい「探す」(`/explore`) と従来の「探す」(`/trends/explore`) が併存している。従来画面には新ページへの案内、新ページには従来画面へ戻るリンクがあることを、実画面と公開DOMで確認した。新UIでは入力のaria-labelは同じだが、roleはcombobox。期間はbuttonとmenuitemradio、関連語は「上位の語句」tableになり、従来のMD-SELECTと `.fe-related-queries` は0件。**新UIに旧期間・関連欄ロケーターが一致しないことを実物で確認した。**

既存コードは従来UIの構造とURLを前提にしている。現在の従来UIでは入力・期間のロケーターは一致しているため、既存ロケーターの文字列が一律に改名されたとの結論ではない。新旧画面の区別と、トップから入る経路への対応が必要。新UIの公開時期、今回のHTTP 429とUI変更の因果関係は確認していない。

トップ中央からの検索は、操作可能確認後に1秒待っても4比較すべてトップに残った。本人提案の上部「調べる」タブ経由では専用profileの従来画面へ遷移し、「スキンケア」の入力・Enter確定後にURLのqも同語になった。しかしHTTP 429が観測され、この条件では取得できなかった。その後、本人が示した新UIをユーザーChromeで直接操作すると20件取得できた。profile・UI経路が異なり、新UIの待機なし対照試験もないため、1秒待機だけが成功原因とは結論しない。

### 説明の誤りと訂正

最初の専用未ログインprofileの `/explore` ではログイン案内を表示し、入力欄0件を観測した。この特定の表示状態から「新ページには入力欄がない」「ログインが必要」と一般化した説明は誤りだった。本人が示した新UIの入力欄画像と、未ログインの従来UIの画像を受け、条件の一般化を明示的に撤回した。最終確認では、ユーザーChromeの `/explore` にcombobox入力があり、検索・取得まで実施できている。ログインが必須かどうかは今回の観測から断定しない。個人名・アカウント識別情報は共有記録へ転記しない。

## ロケーターの具体的な照合

| 要素 | 既存コード／今回の指定 | 現行画面での結果 |
|---|---|---|
| トップ中央入力 | `getByLabel('検索', {exact:true})` 後にinputを判別 | 従来の観測も現在もlabel一致2件・実input1件。入力できる |
| トップ中央の送信 | `getByRole('button', {name:'調べる',exact:true})` | 中央のBUTTON1件。有効でクリック完了するが4比較で遷移しない |
| トップ上部のタブ | `getByRole('tab', {name:'調べる',exact:true})` | BUTTON要素だがrole=tab。中央のbutton指定とは別。1件一致し `/trends/explore` へ遷移成功 |
| 従来版の入力 | `getByLabel('検索キーワードを追加', {exact:true})` | 1件一致。type=search、同じaria-labelとplaceholder。入力・確定とも成功 |
| 従来版の期間 | `[aria-label^="Select time period"]` | 1件一致。MD-SELECT / role=listbox。今回は「過去12か月間」を表示 |
| 従来版の検索種別 | `[aria-label^="Select stories location"]` | 1件一致。「ウェブ検索」を表示 |
| 従来版の商品語句種別 | 可視の完全一致「検索キーワード」 | 1件一致 |
| 従来版の関連欄 | `.fe-related-queries` と見出しによる種類判別 | class自体は2件一致するが地域欄と関連トピック。可視の「関連キーワード」は0件。429とエラー表示があるため、関連語欄の欠落をロケーター変更と断定しない |
| 従来版のTop切替 | 対象関連語欄内の `md-select.bullets-view-selector` | 地域欄には存在する。関連語欄のものは確認不能。地域欄を関連語と誤採用しない |
| 新ページのURL | 既存コードの遷移待ちは `/trends/explore` | 公開リンク「新しい［探す］ページに移動」の先は `/explore`。既存のURL条件と異なる |
| 新ページの入力 | `getByRole('combobox',{name:'検索キーワードを追加',exact:true})` | ユーザーChromeでINPUT / role=combobox / 同名aria-labelを1件確認し入力成功。候補option「スキンケア 検索キーワード」をクリックして確定 |
| 新ページの期間 | 旧 `[aria-label^="Select time period"]` | 旧指定は0件。現在はbutton「過去 1 年間、期間を選択」→menuitemradio「過去 3 か月間」で変更成功 |
| 新ページの地域 | button「すべての国、地域を選択」 | menuitemradio「日本」は地域サブメニューを開く。menuitem「すべての地域」まで選んでbutton「日本、地域を選択」への反映を確認 |
| 新ページの関連語 | 旧 `.fe-related-queries` | 旧指定は0件。table「上位の語句」のrowで1～10、対象tableのcontainer内のbutton「次のページに移動」で11～20を確認 |

空表示についても、既存処理が受け付ける2文言と異なる「ここに表示するデータはありません。」を関連トピック欄で観測した。関連キーワード欄の正常な0件を確認できたわけではなく、今回この文言を正常終了条件には追加していない。

既存コードの比較先は `automation/inago/adapters/public_sources.mjs`。過去commit `c893107` と現行版の入力・button・期間・関連欄指定を照合し、これらの文字列は同じだった。過去の実画面の完全DOMは保存されておらず、UI変更の日時や当時の上部タブのroleまでは確定できない。

## 1秒待機の実測

`probe.mjs` は既存公開UIアダプターのmainをそのままimportし、この診断process内だけlocator click/fill/pressを包む。trial clickで表示・安定・enabled・クリックを受け付ける状態を確認し、入力はeditableも確認した後、1000ms待ち、本操作を行う。観測読取はbest-effortで操作結果を上書きしない。診断読取時間もあるため、表の値は操作可能確認から実操作までの待機時間の下限。

| 比較 | JST | 入力／送信 | 実測の入力前／送信前待機 | 実結果 | exit |
|---|---|---|---|---|---:|
| 1 | 11:52:19–11:52:54 | fill／中央button | 1010ms／1000ms | トップから遷移せず、遷移待ち30秒TimeoutError | 1 |
| 2・UI計測追加 | 11:54:13–11:54:48 | fill／中央button | 1011ms／1007ms | 同上。DOM complete、button enabled、クリック完了 | 1 |
| 3 | 11:55:56–11:56:30 | fill／Enter | 1003ms／1009ms | 同上。入力のvalueはスキンケア | 1 |
| 4 | 11:57:05–11:57:40 | 逐次文字入力／中央button | 1005ms／1007ms | 同上。日本語IME操作との同一性は保証しない | 1 |

比較1～4では429を観測していない。公開sourceのCLI境界ではTimeoutErrorをsource_timeoutへ分類するが、この直接main呼出しの生結果はTimeoutErrorとして保持した。

本人提案の上部タブ経路は対話型Nodeで実施。タブ1014ms・入力1012ms・Enter1002msの待機後に実操作。`/trends/explore`、q=スキンケア、入力値も同語を確認。429は空の従来画面で1件、語句確定後を含め計2件観測した。期間変更・Top切替・ページ送りは取得エラーの後には実施していない。新UIへの公開リンクは1009ms待機後にクリックし、`/explore` とログイン案内を確認した。ログイン操作は行っていない。

### 本人画像と同じ新UIでの取得

computer-useの手順を参照し、CUAのChrome extensionから本人が開いている該当Trendsタブだけを選択した。ログイン操作・profileの複製・認証情報の読取りは行っていない。公開DOMで確認したlocatorに対して表示・enabledを確認し、操作前1000msを挟んだ。

1. combobox「検索キーワードを追加」にスキンケアを入力。
2. option「スキンケア 検索キーワード」を選択。トピックや薬物種類の同名候補と区別。
3. 地域buttonから日本のサブメニューを開き、「すべての地域」まで選択して日本全体を確定。
4. 期間buttonから「過去 3 か月間」を確定。ウェブ検索を確認。
5. table「上位の語句」の可視順位1～10と原文を読む。増加傾向・地域別の表と区別。
6. 公開DOMで対象tableの親containerを照合し、その中の「次のページに移動」だけをクリック。順位11～20の更新と原文を確認。

地域「日本」のクリックだけではサブメニューが開き、国設定は未確定だったことも観測した。選択ボタンが「日本、地域を選択」になったことを確定根拠とし、途中の操作完了を設定成功と取り違えていない。

最終URLは `/explore`、date=today 3-m、geo=JP。qは新UIが二重percent encodingで保存しており、旧URLパーサーが単回decode前提の場合は追加照合が必要。画面のスキンケア検索キーワード確定表示も確認した。結果は関連Top20の生観測であり、テーマ適合・意味重複の判定や日次DBへの採用は行っていない。

本人からヘッドあり表示の依頼後、該当タブがactiveであることを確認し、管理APIでそのChromeウィンドウをfocused=trueにした。最初にtabs.update(active)を試した操作はAPI未対応で変更されず、その後のwindows.updateで前面化に成功。本人の他タブ・ウィンドウの移動や終了はしていない。最終取得画像は非公開の `new-ui-success.png`。

## 再現入口と版

作業場所は `C:/project/rakuten-room-inago`。開始時git statusは空、HEADはe2083ce。アダプターSHA256は `62f99ebdb8791f52587988bdb9ad9d3b23326386301e91ff6282c650d58615b5` で全比較中不変。診断scriptの最終hashと各実測は `results.json` に保存した。

診断の実行コマンド（各条件を混同しない）：

```powershell
node worklogs/projects/maintenance/trends-wait-20261021/probe.mjs
node worklogs/projects/maintenance/trends-wait-20261021/probe.mjs --submit-enter
node worklogs/projects/maintenance/trends-wait-20261021/probe.mjs --type-keys
node worklogs/projects/maintenance/trends-wait-20261021/probe.mjs --self-check --type-keys
node --check worklogs/projects/maintenance/trends-wait-20261021/probe.mjs
```

上部タブ経路の実操作は、同じ設定の専用persistent profileを起動した対話型Nodeで次の順序。各click/fill/pressの直前にtrial click→1000ms待機を挟んだ。

```javascript
await page.goto('https://trends.google.co.jp/trends/', {waitUntil:'domcontentloaded'});
await page.getByRole('tab', {name:'調べる',exact:true}).click();
await page.waitForURL(url => /\/trends\/explore\/?$/.test(url.pathname));
await page.getByLabel('検索キーワードを追加', {exact:true}).fill('スキンケア');
await page.getByLabel('検索キーワードを追加', {exact:true}).press('Enter');
// 現行DOM、URLのq、公開responseのstatus/typeを保存。
// 別の新UI照合では上部タブから入り、公開の移行リンクをクリック。
await page.getByRole('link', {name:'新しい [探す] ページに移動',exact:true}).click();
```

生stdout/stderr、終了画面、公開DOM照合はgitignore対象の `非公開作業データ/trends-wait-20261021/`。初回起動は必須fixture delay=0引数がなくinvalid_request（ブラウザー未起動）になり訂正した。Enter別経路の初回もlabelとinputの多重一致で送信前に失敗し、and(input)で限定した。これら診断script自身の失敗も別名で保持し、取得失敗の4比較には含めない。

## 検証・レビュー・適用範囲

- 最終の待機機構確認：無効buttonが有効になるまで660ms待ち、その後1013ms待ってクリック。逐次入力1003ms、Enter1002msを確認。3操作すべてcomplete、exit 0。試験は1秒待機を検証するもので、Trends取得成功を証明しない。
- Node構文検査exit 0。製品コード変更がないため、無関係な448件の日次回帰試験は再実行しない。
- code-reviewerは必須入力欠落・診断読取失敗の誤分類を指摘し修正後Approve。逐次文字入力と実IME操作の同一性は主張しないとの指摘も反映。
- 比較1～4と従来UIの調査は同じ専用公開profileを使い、所有するbrowser contextと対話型Nodeは終了済み。その後は本人画像の実画面を照合するため、CUAから既に開いているユーザーChromeのTrendsタブを操作した。認証切替、API直接取得、アクセス制限回避は行っていない。ユーザーChromeの検証タブは本人が見られるよう維持した。
- 日次設定・製品コード・運用DB・13時起動予定は変更していない。1秒待機は診断process内だけで、本番採用済みとは扱わない。
- 最終4ファイルをcode-reviewerが再照合しApprove。20語の順位1～20、条件、URLの追加decode、probe hash、初期4比較の全待機1000ms以上と失敗終了が証拠に一致することを独立確認した。レビュー側がUI操作を実行したという主張はしない。
- 最終の共有JSON機械照合は、20件・連番・原文重複なし・検索条件・script hash・製品source不変を確認してexit 0。最初のPowerShell照合は配列の `.rank` が配列自身のRank属性を指し、連番比較が誤って失敗した（exit 1）。各行のrankをForEach-Objectで明示取得するよう照合コマンドを訂正し、同じJSONのまま合格を確認。元データを書き換えて整合させていない。

## 自己評価

agent-self-evaluationの5軸で確認、平均3.4/5。

| 軸 | 評点 | 根拠と限界 |
|---|---:|---|
| 正確性 | 3 | 専用profileの表示を一般化した説明が誤りだった。明示撤回し、本人画像と同じ実タブで入力・期間・関連表・20件を確認して修正した |
| 完全性 | 4 | 1秒比較・タブ経路・新旧UI・新UIの20語取得と見える画面まで確認。本番への実装反映は今回の試行と区別 |
| 明瞭さ | 3 | 初期説明で新旧画面と表示条件を混同した。最終記録では冒頭に成功条件と訂正を置き、locator照合表を更新した |
| 実用性 | 4 | 現行の期間・地域・表・ページ送りとURL形式を実物で確認。新UI未ログイン時の全条件と本番組込みは未検証 |
| 簡潔さ | 3 | 当初、失敗した同じ入口の比較が長く、本人の指摘への直接照合が遅れた。最終回答はUI差分と実取得結果だけに絞る |

改善は、(1) 表示条件を一般化せず本人の実画面を先に照合する、(2) 待機の効果とUI経路・profileの効果を区別する、(3) 将来製品へ反映する際に新旧UI fixtureとURLの二重decode検証を追加する。低評価の説明誤りは今回の記録と回答で訂正した。本人は当初の誤説明を指摘しており、高評価で隠さず、実画面の20件という確認事実を判断材料とする。
