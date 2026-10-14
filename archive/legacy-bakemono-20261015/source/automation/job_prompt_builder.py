"""Build a small path-based prompt without conversation or credentials."""

from pathlib import Path


_FORBIDDEN_NAMES = {"rakuten-api.credentials.json", "auth-state.json", "calendar.json", "credentials.json"}


def build_job_prompt(subskill: Path, envelope: Path, input_path: Path,
                     output_schema: Path, output_path: Path,
                     template_path: Path | None = None) -> str:
    paths = [Path(item).resolve() for item in (subskill, envelope, input_path, output_schema, output_path)]
    if any(path.name.lower() in _FORBIDDEN_NAMES for path in paths):
        raise ValueError("credential-named path cannot be used in a model job")
    if not paths[0].is_file() or not paths[2].is_file() or not paths[3].is_file():
        raise ValueError("required job input, skill, or schema is missing")
    if not paths[1].is_file():
        raise ValueError("job envelope is missing")
    skill, job, input_json, schema, output = paths
    if template_path is not None:
        template_path = Path(template_path).resolve()
        if not template_path.is_file() or template_path.name.lower() in _FORBIDDEN_NAMES:
            raise ValueError("job template is missing or forbidden")
    template_line = f"ジョブテンプレート: {template_path}\n" if template_path else ""
    return (f"担当スキル: {skill}\n"
            + template_line
            + f"ジョブ契約JSON: {job}\n"
            f"入力成果物JSON: {input_json}\n"
            f"出力Schema: {schema}\n"
            f"出力先: {output}\n"
            "担当範囲だけ実行し、Schemaに適合するJSONを最後の回答に出す。"
            "入力にない商品や観測を作らない。正本を直接書き換えない。"
            "認証情報を入力・出力しない。投稿やアカウント操作をしない。\n")
