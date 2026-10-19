# FC10 resume 19: 実HTMLレビューで見つかった共通レンダラーの残件

- 記録時刻: 2026-10-20 06:38 JST（2026-10-19 21:38 UTC）
- 対象: `job-historical-9e7f0c175cc4c874c955a5dab29c4427` / launch `historical-9e7f0c175cc4c874c955a5dab29c4427` / 2026-10-17 / `source_run_id=null`
- 基点: branch `main`、HEAD `19cd48860408c7bae3eda8a758a0cb72edd8e9f9`、tracking `origin/main`。この追補の差分は未commit・未push。
- job確認: DB `quick_check=ok`。同じjobは`resume_count=19`、`status=running`。manifestは指定launch、対象日、`source_run_id=null`、SHA-256 `e97b191fdde1eb2f178c53bf51a5ad61979b69b80868a9a31b8accf31ab48d30`。報告workerを中断したため、報告工程は開始記録後のままである。別jobの作成や日次runの生成はしていない。

## 実HTMLの独立レビュー

resume 19で実際に生成されたHTMLを独立report reviewerが確認した。

- r9 `report-69b06174df8c6a7a457cc70c-r9`、HTML SHA-256 `6b88b53c163844519613bf2ca53aa0d1f902e7b1d2505dcfd47b72bf78bcd453` は`changes_requested`、4件。試験未実施の工程を「試験で確認」と分類、報告付録のレビュー・試験版不足と翌日確認表記の不一致、調査結果作成済みと承認済みの混同、印刷時の工程図横はみ出し。
- r10 `report-69b06174df8c6a7a457cc70c-r10`、HTML SHA-256 `3b35b88d96b90329e067c2f72f7009ca76acc385c862844eba096b43cdb83efd` は`changes_requested`、2件。本文に内部項目名とnullが見えるため平易な言葉へ直すこと、印刷時の工程図横はみ出し。
- r9/r10の実レビューはreport workerを止めた時点でjob DBへ保存されていなかったため、この記録へ元のHTML版・hash・指摘数を復元して記録した。各指摘を承認・解消済みとしては扱わない。

## 修正

- 実際のjobの`source_run_id`がnullの場合だけ、完全なnull表現を「日次の実行記録との紐づけはありません。」へ置き換える。値があるjobではその断定をしない。否定・限定表現は一致させず、内部名検出で一般的な参考資料案内へ置き換える。
- 内部項目名の境界検出を日本語の隣でも働かせる。技術付録では値の取得失敗を示唆せず、`source_run_id: null`または実値を正確に表示する。
- 印刷時の工程図を折り返し、横overflowをなくす。
- reporter/reviewer prompt、report標準、skill/contract、roles、要件辞書・追跡表・canonical要件HTMLを同期した。要件HTML builder v2.55は28章、39図、26参照資料、4,187,457 bytesでPASS。

## 検証と独立製品レビュー

- TDD RED: 正常なnull、実値があるjob、否定形nullの3ケースを追加。最初の修正前実行では、付録値の契約不一致は期待どおり検出したが、non-nullケースはresponse identityを更新し忘れたfixture不整合で検証前に失敗した。fixtureを直したnon-null動作の修正前REDは測定していない。否定形ケースは修正後に追加し、REDで不正な肯定文が残ることを確認してから直した。
- 修正後の回帰4件（null、non-null、否定形、C45印刷契約）は**4 passed / 0.27秒**。
- report rendererとpublicationの対象2 module全体は**59 passed / 78.45秒**。Python `py_compile`、reporter/reviewer TOML 2件parse、要件HTML builder、`git diff --check`はいずれもPASS。diff checkは既存CRLF/LF警告のみ。
- 独立製品reviewerは初回に「実jobがnon-nullでもnullと断定する可能性」「付録に正確なfield名/valueがない」を指摘。修正後、否定文を途中一致して誤変換する追加問題を指摘。完全なnull表現だけに限定し、否定形を安全な案内へ戻す修正を加えた。最新版の独立製品reviewはAPPROVE、指摘0件。review対象hash: `reporting.py` `BE37F17D4437926F94932B189802A6F6F8FCAAE59FE26140B914B7FE56429058`、template `A36B5284B7B0C123B4D204168A7FC5A855974DB80E623EBD6A9122683207EAAA`。
- 実HTML reviewの保存版は`report-live-review-r9-r10.json`、製品コードreviewは`renderer-resume19-review.json/.md`。

## 現在地と再開位置

この差分は未commit、未push、本番未適用である。次はtask所有差分だけを選択stageし、秘密情報とindexを検査して`main`の`origin/main`へ公開する。固定workspaceの公式runtime switcherで適用・健康状態・task/readbackを確認した後、同じjob/launchをresume 20し、明示source launch指定のWorkerだけを実行する。fresh reportを同じjob内で再reviewし、作成者の修正、独立review、公開、DB/remote readback、正しいterminal記録を確認する。10/19のqueue/history/Dailyは処理しない。

この時点ではFC-A06/A07および計画全体を完了としない。元の10/17原因も不明のままである。
