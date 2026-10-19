"""Plain-language report fallback used when the normal reporter cannot finish."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import html
from typing import Any, Mapping


@dataclass(frozen=True)
class FallbackReport:
    html: str
    sha256: str
    report_version: str


def render_report_examples() -> dict[str, str]:
    """Return visibly synthetic examples with evidence and truthful stage states."""

    common = {
        "job_id": "example-job",
        "source_launch_id": "example-launch",
        "target_date": "2026-10-17",
        "incident_id": "example-incident",
    }
    shared = {
        "synthetic_example": True,
        "event_category": "試験で確認",
        "evidence_type": "試験用に作った合成記録",
        "review_status": "この例の想定では、別の担当者による確認はまだ行われていません。",
        "publication_status": "この例の想定では、まだ公開していません。",
        "workflow_status": "この例の想定では、工程全体の終了登録前です。",
        "incident_resolution": "元の不具合が直ったかは、このシナリオでも未確認です。",
    }
    cases = {
        "通常": {
            **shared,
            "operation": "10月17日の日次処理で、予定した3件のうち2件だけ記録に残った合成例です。",
            "event_classification": "日次処理の一部が記録されていない例",
            "expected_behavior": "合成例では、予定した3件すべてが結果に残る想定です。",
            "actual_behavior": "この表示例では、試験用に作った記録は2件で、1件分が見つかりません。",
            "impact": "この例だけでは、記録がない1件が利用者へ影響したか分かりません。",
            "recovery": "再実行や回復の記録は、この例では確認していません。",
            "actual_changes": "この例では元の製品コードを変更していません。",
            "test_scope": "3件を入力し2件だけ結果に残る、試験用に作った記録の例です。製品試験の結果ではありません。",
            "known_scope": "予定3件の合成記録と、結果2件の合成記録を見比べた例です。",
            "unknowns": ["記録が1件不足した原因と、利用者への実際の影響は分かりません。"],
            "next_action": "同じ処理に結び付いた元の記録で、不足した1件と影響を確認します。",
            "evidence": [("報告書作成ルール", "docs/standards/イナゴ日次不具合レポート作成ルール.md"), ("代替報告の作成処理", "automation/inago/maintenance/report_fallback.py")] ,
        },
        "全項目不明": {
            **shared,
            "operation": "10月17日の処理が完了したかを、この合成例の資料だけでは判断できない例です。",
            "event_category": "調査で判明",
            "event_classification": "処理結果の一部が不明な例。原因の分類は未確定です。",
            "expected_behavior": "所定の処理結果と終了記録がそろう想定ですが、この例では期待値の元記録を確認できません。",
            "actual_behavior": "例示記録には処理の開始があり、終了を示す記録はありません。一方、別の調査記録には呼び出し条件や失敗の説明が残っています。",
            "impact": "開始・終了記録の範囲は分かりますが、利用者への影響や不足件数はまだ分かりません。",
            "recovery": "再実行や回復を示す記録は、この例の参照範囲にはありません。",
            "actual_changes": "この例では元の製品コードを変更していません。",
            "test_scope": "調査記録と開始・終了を示す試験用の記録を見比べる説明例です。製品試験ではありません。",
            "known_scope": "確認できたのは処理開始記録があること、終了記録が見つからないこと、調査記録に原因候補を説明する材料があることです。",
            "unknowns": ["処理が実際にどこまで進んだか、原因が確定したか、利用者への影響、回復の有無は確認できていません。"],
            "next_action": "調査記録にある原因候補を元記録と突き合わせ、処理の終了と影響を確認します。",
            "evidence": [("保守対応の受入契約", "docs/specifications/inago/AT-MAINT-01_保守対応要件受入契約.md"), ("報告書作成ルール", "docs/standards/イナゴ日次不具合レポート作成ルール.md"), ("代替報告の作成処理", "automation/inago/maintenance/report_fallback.py")],
        },
        "変更なし": {
            **shared,
            "operation": "10月17日の不具合について、原因の手掛かりが足りず、すぐに安全な修正を決められない合成例です。",
            "event_category": "調査で判明",
            "event_classification": "原因未確定。修正要否も確認中です。",
            "expected_behavior": "日次処理の原因、実際の結果、影響を証拠で説明できる状態を目指します。",
            "actual_behavior": "この例では要約記録だけがあり、失敗した処理へつながる詳しい記録がありません。",
            "impact": "実際の影響範囲は記録から確かめられません。",
            "recovery": "回復を示す記録もありません。",
            "actual_changes": "原因を特定できないため製品コードは変更していません。処理を結ぶ番号や呼び出した側の情報を受け取る場所が見つからず、その場だけに記録を足しても何が原因か分からないため、記録の追加もしていません。次は、処理を結ぶ情報を渡せる場所を調べ、見つかったら必要な記録を追加する計画です。",
            "test_scope": "要約記録だけで、発生地点へ結ぶ追跡情報がない試験用の例です。製品試験ではありません。",
            "known_scope": "分かっているのは、現在見える証拠が要約ログだけという範囲です。",
            "unknowns": ["原因、期待結果との差、影響、回復は未確認です。呼出元から失敗地点までの情報を渡せる接続点も未特定です。"],
            "next_action": "同じ処理の流れを確認し、処理を結ぶ情報と失敗の内容を一緒に残せる場所を探します。",
            "evidence": [("共通トレース契約", "docs/specifications/inago/AT-TRACE-01_共通トレース契約.md"), ("代替報告の作成処理", "automation/inago/maintenance/report_fallback.py")],
        },
        "通常renderer失敗": {
            **shared,
            "operation": "10月17日の不具合について、通常の報告書作成が失敗し、短い代わりの報告を用意する合成例です。",
            "event_classification": "通常の報告書作成の失敗です。元の日次障害の分類とは別の出来事です。",
            "expected_behavior": "使える日次記録を説明する報告書を作り、別担当の確認と公開を試します。",
            "actual_behavior": "通常の報告書を作る機能が失敗しました。この短い報告には、開始記録が見つかったこと、終了記録が見つからないこと、通常形式を作れなかったことを載せています。",
            "impact": "通常形式の報告書は作成できませんでした。元の日次処理が利用者へ与えた影響は、別の根拠がないため不明です。",
            "recovery": "短い報告を作成したという合成例です。別担当による確認と公開は、まだ行っていません。",
            "actual_changes": "元の日次処理の製品コードは変えていません。確認できた事実を短い報告ページにまとめました。",
            "test_scope": "試験用の記録を使い、通常の報告書作成が失敗して短い報告を作った説明例です。製品試験ではありません。",
            "known_scope": "通常形式の報告作成の失敗、短い報告の作成、参照できた処理開始の記録が確認済みという設定です。",
            "unknowns": ["日次処理の原因、終了状態、利用者影響、業務上の回復は未確認です。"],
            "next_action": "この短い報告を別担当に確認してもらい、その後の公開結果を別記録で確かめます。",
            "evidence": [("報告工程の実装", "automation/inago/maintenance/production_workflow.py"), ("代替報告の作成処理", "automation/inago/maintenance/report_fallback.py")],
        },
    }
    rendered: dict[str, str] = {}
    for name, case in cases.items():
        rendered[name] = build_fallback_report(
            identity=common,
            **case,
            technical_reference="このHTMLは表示例です。実際の調査結果ではありません。",
        ).html
    return rendered


def build_fallback_report(
    *,
    identity: Mapping[str, Any],
    operation: str,
    outcome: str = "",
    unknowns: list[str],
    next_action: str,
    technical_reference: str,
    revision_findings: list[str] | None = None,
    event_classification: str = "事象の分類は、利用できる根拠だけでは決まっていません。",
    expected_behavior: str = "本来期待された動きは、確認できる元記録がありません。",
    actual_behavior: str | None = None,
    impact: str = "利用者や業務への影響は、利用できる記録からは分かりません。",
    recovery: str = "回復したことを示す記録は確認できていません。",
    actual_changes: str = "この報告の作成中には、元の製品コードを変更していません。",
    test_scope: str = "この報告工程で新しい製品試験は行っていません。前段の試験記録があれば参考付録で確認してください。",
    evidence_type: str = "同じ処理に結び付いた調査・試験・反映記録",
    evidence: list[tuple[str, str]] | None = None,
    known_scope: str | None = None,
    review_status: str = "このページを作成した時点では別担当の確認前です。結果は別記録へ保存します。",
    publication_status: str = "このページを作成した時点では公開前です。結果は別記録へ保存します。",
    workflow_status: str = "このHTMLの作成時点では工程全体の終了登録前です。",
    incident_resolution: str = "元の不具合が解消したかは確認できていません。",
    synthetic_example: bool = False,
    event_category: str = "調査で判明",
) -> FallbackReport:
    """Create a useful Japanese report without claiming unavailable facts."""

    required = ("job_id", "source_launch_id", "target_date", "incident_id")
    missing_identity = [key for key in required if not isinstance(identity.get(key), str) or not identity[key]]
    target_date = identity.get("target_date") if isinstance(identity.get("target_date"), str) and identity.get("target_date") else "確認できていません"
    escaped_unknowns = "".join(f"<li>{html.escape(item)}</li>" for item in unknowns)
    evidence = evidence or []
    evidence_links = "".join(
        f'<li><a href="#evidence-{index}">{html.escape(label)}</a></li>'
        for index, (label, _) in enumerate(evidence, start=1)
    ) or "<li>参照できる資料は確認されていません。</li>"
    evidence_details = "".join(
        f'<li id="evidence-{index}"><strong>{html.escape(label)}</strong>: '
        f'<code>{html.escape(reference)}</code></li>'
        for index, (label, reference) in enumerate(evidence, start=1)
    ) or "<li>参照資料なし。</li>"
    revision_text = (
        "別担当から指摘を受けました。以下に内容を記録しています。修正を試しましたが、反映できたかは確認前です。"
        if revision_findings
        else (
            "この合成例では別担当の確認を行っていません。"
            if synthetic_example
            else "この報告ページを作成した時点では別担当の確認前です。結果はページを変えず、別の記録へ保存します。"
        )
    )
    synthetic_banner = (
        '<p class="synthetic">合成したシナリオ例です。実際の2026年10月17日の調査結果ではありません。</p>'
        if synthetic_example
        else ""
    )
    actual_behavior = actual_behavior or f"{operation} {outcome}"
    finding_items = "".join(f"<li>{html.escape(item)}</li>" for item in (revision_findings or []))
    identity_notice = (f"<p><strong>対象情報:</strong> 一部の識別情報を確認できません（{html.escape(', '.join(missing_identity))}）。分かる項目だけを使って記録を続けています。</p>" if missing_identity else "")
    body = f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>日次作業の不具合対応について</title>
<style>body{{font-family:system-ui,sans-serif;line-height:1.8;max-width:920px;margin:2rem auto;padding:0 1rem;color:#17202a}}h1,h2{{line-height:1.4}}section{{margin:1.5rem 0}}.note{{background:#f1f5f9;padding:1rem;border-radius:.5rem}}.synthetic{{border:2px solid #7c2d12;background:#fff7ed;padding:.8rem;font-weight:700}}pre,code{{white-space:pre-wrap;overflow-wrap:anywhere}}@media print{{body{{max-width:none;margin:0}}}}</style>
</head><body><main>
<h1>日次作業の不具合対応について</h1>
{synthetic_banner}
<p>対象日: {html.escape(target_date)}。<strong>出来事の区分:</strong> {html.escape(event_category)}。<strong>内容:</strong> {html.escape(event_classification)}</p>{identity_notice}
<section><h2>何が起きたか</h2><p>{html.escape(operation)}</p><p><strong>期待:</strong> {html.escape(expected_behavior)}</p><p><strong>実際:</strong> {html.escape(actual_behavior)}</p></section>
<section><h2>影響と回復</h2><p><strong>影響:</strong> {html.escape(impact)}</p><p><strong>回復:</strong> {html.escape(recovery)}</p></section>
<section><h2>何を調べ、何を変えたか</h2><p><strong>確認した範囲:</strong> {html.escape(known_scope or outcome)}</p><p><strong>変更:</strong> {html.escape(actual_changes)}</p><p><strong>試験の範囲:</strong> {html.escape(test_scope)}</p></section>
<section><h2>確認した記録</h2><p>記録の種類: {html.escape(evidence_type)}</p><ul>{evidence_links}</ul></section>
<section><h2>分かっていないこと</h2><ul>{escaped_unknowns or '<li>確認できた不明点はありません。追加で調べる必要がないかは別途確認します。</li>'}</ul></section>
<section><h2>レビューと公開</h2><p>{html.escape(review_status)} {html.escape(revision_text)}</p>{("<ul>" + finding_items + "</ul>") if finding_items else ""}<p>{html.escape(publication_status)}</p></section>
<section><h2>工程の完了と元の不具合</h2><p><strong>自動対応の工程:</strong> {html.escape(workflow_status)}</p><p><strong>元の不具合:</strong> {html.escape(incident_resolution)}</p></section>
<section><h2>次に行うこと</h2><p>{html.escape(next_action)}</p></section>
<details class="note" open><summary>参考付録：内部記録と根拠の参照先</summary><ul>{evidence_details}</ul><p>確認できなかった対象情報: {html.escape(", ".join(missing_identity) if missing_identity else "なし")}</p><pre>{html.escape(technical_reference)}</pre></details>
</main></body></html>"""
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return FallbackReport(body, digest, f"fallback-{digest[:20]}")
