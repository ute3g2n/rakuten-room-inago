"""Keep job prompt templates and skill guides aligned with executable contracts."""

from pathlib import Path

from automation.jobs.contracts import CONTRACTS


DETAILS = {
    "research_plan": ("5ジャンルの在庫不足と成果条件から検索計画を作る", "不足数、勝ち条件、優先度低下、調査キューをすべて読む。保留商品を除外しない。商品名や需要を捏造しない。不足があるジャンルごとに悩み・用途・属性を変えた重複しない検索語を5〜8件作る。", "5ジャンルの優先順位と検索語が不足数・根拠に対応する"),
    "triage": ("API候補から商品ページ確認対象を選ぶ", "重複・投稿済みを除き、確認対象を最大15件に絞る。API候補を適格確定しない。", "選択・除外・不確実の理由が候補IDに対応する"),
    "product_verify": ("実商品ページsnapshotから適格性を判定する", "商品名、型番、販売状態、価格条件、レビュー、確度を確認する。heat_scoresが入力された場合は、6項目の点数と根拠をそのまま使い、再採点しない。nullは0点にせず未確認として扱う。取得失敗を推測で補わない。", "確認済み根拠を持つ商品だけをverifiedに入れ、点数と販売状態の判断を混同しない"),
    "copywriter": ("確認済み商品から日本語投稿文を作る", "管理IDは外部表示にとどめ本文へ入れない。根拠のないNo.1、効能断定、購入強要を避ける。指定実験があれば1変数だけ変える。", "商品IDとURLが確認済み入力に一致し、本文がそのままコピー可能"),
    "quality_gate": ("別ジョブで投稿文の根拠と表現を検査する", "ID、名前、URL、本文、根拠、NG表現、実験の単一変更を照合する。不合格理由と再作成対象を構造化する。", "合格と不合格が理由付きで分かれ、原稿を自分で書き換えない"),
    "daily_plan": ("確認済み未投稿下書きから日次計画を組む", "5ジャンル6/2/2/3/2で最大15件。ID/URL重複、投稿済み、本文欠落を除く。条件を反映し不足時は実数と理由を示す。", "予定がローカル制約検査を通り、成果条件の適用を追跡できる"),
    "performance": ("投稿別反応と集計成果を分けて学習する", "第19節の帰属規則を守る。日別クリックを投稿へ配らない。いいね0を購入0やCへ変えない。9配列を欠落させない。", "A/B/C/保留の根拠と翌日の調査・計画条件を出す"),
    "recovery": ("失敗したrunと工程だけを診断する", "認証、CAPTCHA、入力不足、取得失敗はモデル昇格で解決しない。無関係工程や正本を触らない。", "対象run_id/step、局所再試行案、利用者対応の要否を明示する"),
}


def generate(root: Path) -> None:
    root = Path(root)
    for name, contract in CONTRACTS.items():
        purpose, rules, success = DETAILS[name]
        required = "、".join(f"`{field}`" for field in contract.required)
        skill = root / ".agents" / "skills" / contract.skill / "SKILL.md"
        content = (f"---\nname: {contract.skill}\ndescription: 楽天ROOM化け物・改の{purpose}。オーケストレータから{name}ジョブを受けたときに使う。\n---\n\n"
                   f"# {purpose}\n\n## 適用範囲と入力\n\n- `{name}`専用。必須入力は{required}。`run_id`と`target_date`はジョブ共通。\n"
                   f"- `rakuten-room-kai/orchestrator/schemas/{contract.schema}.schema.json`を出力Schemaとする。\n"
                   "- 参照: `../rakuten-room-bakemono-kai/references/data-policy.md`、`../rakuten-room-bakemono-kai/references/output-contracts.md`。成果帰属は設計書第19節を優先。\n\n"
                   f"## 判断規則\n\n{rules}\n\n## 禁止と成功条件\n\n"
                   "- 正本の台帳、下書き、投稿管理HTML、投稿状態、実行台帳を直接更新しない。認証情報を読まない。投稿・いいね・フォロー・購入・ログインをしない。\n"
                   "- 入力にない商品・価格・反応・売上・根拠を補わない。欠落は欠落として示す。モデル名と推論量はmodel-policy.jsonに従う。\n"
                   f"- 成功: {success}。Schema適合JSONだけを返す。\n")
        skill.write_text(content, encoding="utf-8")
        prompt = root / "automation" / "jobs" / "prompts" / f"{name}.md"
        prompt.parent.mkdir(parents=True, exist_ok=True)
        prompt.write_text(f"# {name}\n\n{purpose}。\n必須入力: {required}。\n判断: {rules}\n成功: {success}。\n"
                          f"出力は`{contract.schema}.schema.json`に適合させる。正本は変更しない。\n", encoding="utf-8")


if __name__ == "__main__":
    generate(Path(__file__).resolve().parents[2])
