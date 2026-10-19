# FC09 独立製品コードレビュー

## 判定

**changes_requested**。対象はmanifest `69cc38f136a4d0f904f24e56c922263439a3b070c6d3e8e31589dcf9d7c5b3a7` が示す39個の製品Python、SQL migration、JSON schemaです。manifest内の全39 source hashは現在の作業ツリーと一致しました。基準commitは `51cfc7503775a25af731c8f4bcf727c94f10a595`、計画の現在SHA-256は `7f306659f6d7d159fec563ba1aafaaf709901c31eb3fb2be719c904c7fa695eb` です。全source hash、実行証拠参照、レビュー版結合は `independent-product-review.json` に記録しています。テストコードはreviewしていません。

## 指摘

1. **Medium — invocation前提不一致が内容ある引継ぎを作らず、workflowをblockedで早期終了します。** [workflow_provider.py](/C:/project/rakuten-room-inago/automation/inago/maintenance/workflow_provider.py) の `MaintenanceWorkflowProvider.__call__` は `_invocation_precondition_error` の値があると、invocation ID採番、実行版捕捉、operation event、stage handoffより前に `WorkerWorkflowOutcome(status="blocked")` を返します。この経路では後続stage/report担当を実起動せず、不一致の具体的理由を含む引継ぎも残りません。FC-R03/R04、FCT01/FCT10の継続・引継ぎ契約と合いません。誤identityで処理しない安全性は保ったまま、safe identityを使った相関failure recordと各stageのnot-performed fallback handoffを保存し、後続reportを起動する必要があります。**2026-10-19のdispatch除外は維持してください。** この分岐を測るfocused test証拠は見当たりません。

## 確認した修正と範囲

- `SCHEMA_VERSION = 15` はmigration 014/015の順序付き登録と一致し、適用はtransaction内でuser_versionとmigration ledgerを更新します。
- migration 014は相関operation table、indexとappend-only triggerを追加します。migration 015はrepair predecessorのall-or-none triggerだけを外し、immutable triggerを残します。正式store API側はsequence 3を保ち、完全なpinを検証し、前段情報が部分欠落なら3列ともNULLでrepairを続ける実装でした。
- stageが返すblocked/waitingはfuture stage statusではunknownへ正規化され、ループ継続します。上の指摘は、その正規化より前にあるinvocation開始の早期returnです。
- trace経路は例外本文、prompt、環境値、SQL parameter、localsを記録せず、例外class/cause class、数値errno、basename/line/function、allowlist化したID/refを保存します。
- workerの `WORKER_DISPATCH_EXCLUDED_DATES = frozenset({"2026-10-19"})` は維持されています。

FC09の最終JUnitはmigration v14→v15と公開後クラッシュ・同一合成job再開でPASSを示します。これらはtemporary SQLite/local bare remoteの測定です。本番DB/jobは未操作であり、このレビューではpytestを再実行していません。指摘を実装担当へ返しつつ、FC10/FC11はreview結果に関係なく続行してください。
