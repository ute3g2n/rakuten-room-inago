"""Build a read-only document index and migration manifest for U03.

The tool never moves or rewrites source documents. It resolves references against
their current paths and projects relative links against the proposed destinations.
"""

from __future__ import annotations

import base64
import csv
import json
import posixpath
import re
import subprocess
from collections import Counter
from datetime import datetime
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urlsplit


ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "非公開作業データ/イナゴ実装試験/maintenance-20261017/U01/移行台帳_原本.csv"
PROPOSAL = ROOT / "docs/architecture/文書整理と日次自動不具合対応の構成案_2026-10-17.html"
MANIFEST = ROOT / "worklogs/projects/maintenance/移行manifest.csv"
INDEX = ROOT / "docs/document-index.json"
PRIVATE_EVIDENCE = ROOT / "非公開作業データ/イナゴ実装試験/maintenance-20261017/U04"

MANIFEST_FIELDS = [
    "document_id",
    "source_path",
    "current_path",
    "planned_destination",
    "role",
    "lifecycle_class",
    "visibility",
    "git_state",
    "added_in",
    "scope_note",
]
U02_ADDITIONS = [
    (
        "DOC-0395",
        "plan/イナゴ戦略/AT-MAINT-01_保守対応要件受入契約.md",
        "docs/specifications/inago/AT-MAINT-01_保守対応要件受入契約.md",
        "説明書・受入契約",
    ),
    (
        "DOC-0396",
        "plan/イナゴ戦略/保守運用設定仕様.md",
        "docs/specifications/inago/保守運用設定仕様.md",
        "説明書・保守運用設定仕様",
    ),
    (
        "DOC-0397",
        "plan/イナゴ戦略/図解/F12.svg",
        "docs/specifications/inago/図解/F12.svg",
        "生成用ソース・保守フロー図",
    ),
]
FUTURE_DOCUMENTS = [
    {
        "document_id": "FUTURE-README-001",
        "planned_path": "docs/README.md",
        "role": "docs文書入口",
        "reason": "U04で新規作成し、整理後の仕様・計画・レポート・作業記録への入口とする。",
    }
]
CONTROL_SOURCES = [
    ("SRC-M", "plans/projects/maintenance/文書整理と日次自動不具合対応_実行計画_2026-10-17.md"),
    ("SRC-ACCEPTANCE", "automation/inago/acceptance.py"),
    ("SRC-SETTINGS", "automation/inago/settings.py"),
    ("SRC-TEST-STEP04", "tests_inago/test_step04.py"),
]
TEXT_EXTENSIONS = {
    ".bat",
    ".cfg",
    ".cmd",
    ".css",
    ".html",
    ".htm",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".mjs",
    ".ps1",
    ".py",
    ".svg",
    ".toml",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
ROOT_RELATIVE_PREFIXES = (
    "archive/",
    "automation/",
    "docs/",
    "plan/",
    "plans/",
    "rakuten-room-inago/",
    "reports/",
    "tests_inago/",
    "tools/",
    "worklogs/",
    "非公開作業データ/",
)
HISTORICAL_PATH_EXCEPTIONS = {
    (
        "DOC-0059",
        "json_inputs",
        "plan/イナゴスキル事前準備/補足検索計画.md",
    ),
    (
        "DOC-0059",
        "json_inputs",
        "plan/イナゴスキル事前準備/補足検索計画.json",
    ),
    (
        "DOC-0059",
        "json_review_ref",
        "plan/イナゴスキル事前準備/レビュー/反例点検.md",
    ),
    (
        "DOC-0059",
        "json_review_ref",
        "plan/イナゴスキル事前準備/レビュー/文型目安レビュー.md",
    ),
}
KNOWN_FILE_EXTENSIONS = {
    ".bat", ".cfg", ".cmd", ".css", ".csv", ".db", ".html", ".htm", ".ini",
    ".jpg", ".jpeg", ".js", ".json", ".log", ".md", ".mjs", ".pdf", ".png",
    ".ps1", ".py", ".svg", ".toml", ".txt", ".xml", ".yaml", ".yml",
}
CODE_PATH_RE = re.compile(
    r"""(?:(?:Path|PurePath|PurePosixPath|open|readFile|writeFile|fileURLToPath|URL)\s*\(\s*(?:new\s+URL\s*\(\s*)?['"]([^'"]+)['"]|(?:from|import)\s+['"]([^'"]+)['"])"""
)
CONFIG_PATH_RE = re.compile(
    r"""(?i)\b[\w.-]*(?:path|file|source|destination|output|input|dir|root)\w*\s*[:=]\s*['"]([^'"]+)['"]"""
)
MARKDOWN_LINK_RE = re.compile("!?\\[[^\\]]*\\]\\((?:<)?([^\\s)>]+)")
MARKDOWN_REF_RE = re.compile("^\\s*\\[[^\\]]+\\]:\\s*<?([^\\s>]+)", re.MULTILINE)
CSS_URL_RE = re.compile("url\\(\\s*['\\\"]?([^'\\\")]+)")
CSS_IMPORT_RE = re.compile(r"@import\s+(?!url\()\s*['\"]?([^'\"\s);]+)", re.IGNORECASE)
JSON_STRING_TOKEN_RE = re.compile(r'"(?:\\.|[^"\\])*"')
ANCHOR_RE = re.compile("\\b(?:id|name)\\s*=\\s*['\\\"]([^'\\\"]+)['\\\"]", re.IGNORECASE)
HEADING_RE = re.compile("^#{1,6}\\s+(.+?)\\s*#*\\s*$", re.MULTILINE)


class InventoryParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[dict[str, str]] = []
        self.row: dict[str, str] | None = None
        self.capture: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "tr" and "data-inventory-source" in values:
            self.row = {
                "source": values["data-inventory-source"] or "",
                "destination": values.get("data-inventory-destination") or "",
                "role": "",
                "scope_note": "",
            }
            self.rows.append(self.row)
        elif self.row and tag == "span" and values.get("class") == "role":
            self.capture = "role"
        elif self.row and tag == "small":
            self.capture = "scope_note"

    def handle_endtag(self, tag: str) -> None:
        if tag in {"span", "small"}:
            self.capture = None
        elif tag == "tr":
            self.row = None
            self.capture = None

    def handle_data(self, data: str) -> None:
        if self.row and self.capture:
            self.row[self.capture] += data


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[tuple[str, str, int]] = []
        self.embedded_json: list[tuple[str, str, int]] = []
        self.json_script_id: str | None = None
        self.json_script_start_line = 0
        self.json_script_data: list[str] = []
        self.in_style = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        line = self.getpos()[0]
        for name in ("href", "src", "poster", "xlink:href"):
            value = values.get(name)
            if value:
                self.items.append((f"html_{name.replace(':', '_')}", value, line))
        srcset = values.get("srcset")
        if srcset:
            for candidate in srcset.split(","):
                url = candidate.strip().split()
                if url:
                    self.items.append(("html_srcset", url[0], line))
        style = values.get("style")
        if style:
            for pattern, kind in ((CSS_URL_RE, "html_style_url"), (CSS_IMPORT_RE, "html_style_import")):
                for match in pattern.finditer(style):
                    self.items.append((kind, match.group(1).strip(), line))
        if tag == "style":
            self.in_style = True
        if tag == "script" and ("json" in (values.get("type") or "").lower() or values.get("id") == "requirements-source"):
            self.json_script_id = values.get("id") or "embedded-json"
            self.json_script_start_line = line
            self.json_script_data = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "style":
            self.in_style = False
        if tag == "script" and self.json_script_id is not None:
            self.embedded_json.append(
                (self.json_script_id, "".join(self.json_script_data), self.json_script_start_line)
            )
            self.json_script_id = None
            self.json_script_data = []

    def handle_data(self, data: str) -> None:
        if self.json_script_id is not None:
            self.json_script_data.append(data)
        if self.in_style:
            line = self.getpos()[0]
            for pattern, kind in ((CSS_URL_RE, "html_style_url"), (CSS_IMPORT_RE, "html_style_import")):
                for match in pattern.finditer(data):
                    self.items.append(
                        (kind, match.group(1).strip(), line + data.count("\n", 0, match.start()))
                    )


def relative_path(value: str) -> str:
    return PurePosixPath(value.replace("\\", "/")).as_posix()


def lifecycle_for(role: str) -> str:
    return "historical" if role == "旧資料" else "current"


def lifecycle_for_path(role: str, path: str, scope_note: str = "") -> str:
    if role == "旧資料":
        return "historical"
    if path.startswith("plan/イナゴ戦略/html-review"):
        return "historical"
    if path.startswith("plan/イナゴスキル事前準備/レビュー/"):
        return "historical"
    if "の履歴" in scope_note or "未採用" in scope_note:
        return "historical"
    if role == "生成用ソース" and not path.startswith("plan/イナゴ戦略/html-src/"):
        return "historical"
    return "current"


def read_catalog() -> dict[str, dict[str, str]]:
    parser = InventoryParser()
    parser.feed(PROPOSAL.read_text(encoding="utf-8"))
    result = {relative_path(row["source"]): row for row in parser.rows}
    if len(result) != len(parser.rows):
        raise ValueError("A inventory has duplicate source paths")
    destinations = [relative_path(row["destination"]) for row in parser.rows]
    if len(set(destinations)) != len(destinations):
        raise ValueError("A inventory has duplicate planned destinations")
    return result


def read_baseline() -> list[dict[str, str]]:
    if not BASELINE.is_file():
        raise FileNotFoundError(f"U01 baseline is unavailable: {BASELINE}")
    with BASELINE.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 394:
        raise ValueError(f"U01 baseline count must be 394, got {len(rows)}")
    return rows


def bootstrap_manifest() -> list[dict[str, str]]:
    baseline = read_baseline()
    catalog = read_catalog()
    proposal_sources = set(catalog)
    baseline_paths = {relative_path(row["current_path"]) for row in baseline}
    proposal_path = relative_path(PROPOSAL.relative_to(ROOT).as_posix())
    if len(baseline_paths) != 394 or len(proposal_sources) != 393:
        raise ValueError("Baseline/A inventory counts do not match the U03 contract")
    if baseline_paths - proposal_sources != {proposal_path}:
        raise ValueError("A inventory does not cover exactly the 393 non-A baseline documents")

    manifest: list[dict[str, str]] = []
    for row in baseline:
        doc_id = row["inventory_id"]
        current = relative_path(row["current_path"])
        if current == proposal_path:
            catalog_row = {
                "destination": current,
                "role": "構成案HTML",
                "scope_note": "M §2.1に従い現位置を維持する。",
            }
        else:
            catalog_row = catalog[current]
        ignored = row["git_state"] != "tracked"
        role = catalog_row["role"]
        manifest.append(
            {
                "document_id": doc_id,
                "source_path": current,
                "current_path": current,
                "planned_destination": relative_path(catalog_row["destination"]),
                "role": role,
                "lifecycle_class": lifecycle_for_path(role, current, catalog_row["scope_note"]),
                "visibility": "local_only" if ignored else "repository",
                "git_state": row["git_state"],
                "added_in": "U01",
                "scope_note": catalog_row["scope_note"],
            }
        )

    for doc_id, current, destination, role in U02_ADDITIONS:
        path = ROOT / current
        if not path.is_file():
            raise FileNotFoundError(f"U02 addition is missing: {current}")
        manifest.append(
            {
                "document_id": doc_id,
                "source_path": current,
                "current_path": current,
                "planned_destination": destination,
                "role": role,
                "lifecycle_class": "current",
                "visibility": "repository",
                "git_state": "tracked",
                "added_in": "U02",
                "scope_note": "U02で追加。Aの2026-10-17提案393件に含まれない増分。",
            }
        )

    manifest.append(
        {
            "document_id": FUTURE_DOCUMENTS[0]["document_id"],
            "source_path": "",
            "current_path": "",
            "planned_destination": FUTURE_DOCUMENTS[0]["planned_path"],
            "role": FUTURE_DOCUMENTS[0]["role"],
            "lifecycle_class": "future",
            "visibility": "repository",
            "git_state": "not_created",
            "added_in": "U04",
            "scope_note": FUTURE_DOCUMENTS[0]["reason"],
        }
    )
    return manifest


def read_manifest() -> list[dict[str, str]]:
    if not MANIFEST.is_file():
        return bootstrap_manifest()
    with MANIFEST.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        return bootstrap_manifest()
    if any(field not in rows[0] for field in MANIFEST_FIELDS):
        raise ValueError("Existing migration manifest has an invalid schema")
    return rows


def validate_manifest_paths(rows: list[dict[str, str]]) -> None:
    """Reject unsafe paths before any manifest-derived filesystem access."""
    root = ROOT.resolve()
    for row in rows:
        for field in ("source_path", "current_path", "planned_destination"):
            value = row.get(field, "")
            if not value:
                continue
            path = PurePosixPath(value)
            if (
                "\\" in value
                or "\x00" in value
                or path.is_absolute()
                or ".." in path.parts
                or re.match(r"^[A-Za-z]:", value)
                or not value.startswith(ROOT_RELATIVE_PREFIXES)
            ):
                raise ValueError(f"Unsafe manifest path in {field}: {row.get('document_id', 'unknown')}")
            resolved = (ROOT / Path(*path.parts)).resolve(strict=False)
            try:
                resolved.relative_to(root)
            except ValueError as exc:
                raise ValueError(
                    f"Manifest path escapes repository in {field}: {row.get('document_id', 'unknown')}"
                ) from exc


def write_manifest(rows: list[dict[str, str]]) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    with MANIFEST.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=MANIFEST_FIELDS,
            lineterminator="\n",
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def extension(path: str) -> str:
    return PurePosixPath(path).suffix.lower()


def looks_like_path(value: str) -> bool:
    value = value.strip()
    parsed = urlsplit(value)
    if parsed.scheme.lower() in {"data", "file", "ftp", "http", "https", "javascript", "mailto", "tel"}:
        return True
    if parsed.netloc or value.startswith(("//", "#", "./", "../", "/")):
        return True
    candidate = parsed.path.replace("\\", "/")
    if candidate.startswith(ROOT_RELATIVE_PREFIXES):
        return True
    return PurePosixPath(candidate).suffix.lower() in KNOWN_FILE_EXTENSIONS


def json_reference_value(value: str) -> str:
    value = value.strip()
    if urlsplit(value).scheme.lower() in {"data", "file", "ftp", "http", "https", "javascript", "mailto", "tel"}:
        return value
    extensions = "|".join(re.escape(ext[1:]) for ext in KNOWN_FILE_EXTENSIONS)
    match = re.match(rf"^(.+?\.(?:{extensions})(?:#[^\s]*)?)(?:\s|$)", value, re.IGNORECASE)
    return match.group(1) if match else value


def enumerate_current_paths() -> set[str]:
    paths: set[str] = set()
    for parent in (ROOT / "docs", ROOT / "plan"):
        for path in parent.rglob("*"):
            if path.is_file():
                rel = path.relative_to(ROOT).as_posix()
                if rel != "docs/document-index.json":
                    paths.add(rel)
    return paths


def git_tracked_paths() -> set[str]:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    return {
        relative_path(item.decode("utf-8"))
        for item in result.stdout.split(b"\0")
        if item
    }


def extract_references(path: str, text: str) -> list[dict[str, str | int]]:
    suffix = extension(path)
    found: list[tuple[str, str, int]] = []
    if suffix in {".html", ".htm", ".svg", ".xml"}:
        parser = LinkParser()
        parser.feed(text)
        found.extend(parser.items)
        if path in {
            "plan/イナゴ戦略/イナゴスキル要件定義書.html",
            "docs/specifications/inago/イナゴスキル要件定義書.html",
        }:
            for script_id, raw_json, start_line in parser.embedded_json:
                if script_id != "requirements-source":
                    continue
                try:
                    payload = json.loads(raw_json)
                except json.JSONDecodeError:
                    continue
                references = payload.get("references") if isinstance(payload, dict) else None
                if not isinstance(references, dict):
                    continue
                key_lines = json_string_key_lines(raw_json)
                for key in references:
                    if isinstance(key, str) and key.lower().endswith(".md"):
                        offset = key_lines.get(key, 0)
                        found.append(("embedded_document", key, start_line + raw_json.count("\n", 0, offset)))
    if suffix == ".md":
        for match in MARKDOWN_LINK_RE.finditer(text):
            found.append(("markdown_link", match.group(1), text.count("\n", 0, match.start()) + 1))
        for match in MARKDOWN_REF_RE.finditer(text):
            found.append(("markdown_reference", match.group(1), text.count("\n", 0, match.start()) + 1))
    if suffix == ".css":
        for pattern, kind in ((CSS_URL_RE, "css_url"), (CSS_IMPORT_RE, "css_import")):
            for match in pattern.finditer(text):
                found.append((kind, match.group(1).strip(), text.count("\n", 0, match.start()) + 1))
    if suffix == ".json":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = None

        tokens = iter(JSON_STRING_TOKEN_RE.finditer(text))

        def next_string_token(expected: str) -> re.Match[str]:
            match = next(tokens)
            if json.loads(match.group(0)) != expected:
                raise ValueError("JSON string token order does not match parsed value")
            return match

        def visit(value: object, key: str = "") -> None:
            if isinstance(value, dict):
                for child_key, child_value in value.items():
                    next_string_token(str(child_key))
                    visit(child_value, str(child_key))
            elif isinstance(value, list):
                for child_value in value:
                    visit(child_value, key)
            elif isinstance(value, str):
                token = next_string_token(value)
                if not looks_like_path(value):
                    return
                line_number = text.count("\n", 0, token.start()) + 1
                if "～" in value:
                    found.append(("json_reference_range", value, line_number))
                else:
                    values = value.split(";") if ";" in value else [value]
                    for item in values:
                        target = json_reference_value(item)
                        if looks_like_path(target):
                            kind = "json_" + (key or "string")
                            if (
                                not PurePosixPath(urlsplit(target).path).suffix
                                and not (ROOT / target.replace("\\", "/")).exists()
                                and (
                                    any(char.isspace() for char in target)
                                    or any(token in target for token in ("以下の", "件本文", "候補資料", "本文・注釈"))
                                )
                            ):
                                kind = "json_reference_group"
                            found.append((kind, target, line_number))

        if payload is not None:
            visit(payload)
    if suffix in {".py", ".js", ".mjs", ".ps1", ".bat", ".cmd", ".toml", ".yaml", ".yml", ".ini", ".cfg"}:
        lines = text.splitlines()
        output_variables = program_output_variables(text)
        for match in CODE_PATH_RE.finditer(text):
            target = match.group(1) or match.group(2)
            line_number = text.count("\n", 0, match.start()) + 1
            line_text = lines[line_number - 1] if line_number <= len(lines) else ""
            kind = (
                "program_output_path"
                if program_output_assignment(text, line_number, output_variables)
                else program_path_kind(line_text)
            )
            found.append((kind, target, line_number))
        for match in CONFIG_PATH_RE.finditer(text):
            line_number = text.count("\n", 0, match.start()) + 1
            line_text = lines[line_number - 1] if line_number <= len(lines) else ""
            if "vm.runInNewContext" in line_text and re.search(r"\bfilename\s*[:=]", line_text):
                continue
            found.append((program_path_kind(line_text), match.group(1), line_number))
    return [
        {"kind": kind, "target": target, "line": line}
        for kind, target, line in sorted(found)
    ]


def json_string_key_lines(text: str) -> dict[str, int]:
    """Return the first source offset for each decoded JSON object key."""
    lines: dict[str, int] = {}
    tokens = iter(JSON_STRING_TOKEN_RE.finditer(text))
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return lines

    def visit(value: object) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                token = next(tokens)
                decoded = json.loads(token.group(0))
                if decoded != key:
                    raise ValueError("JSON key token order does not match parsed value")
                lines.setdefault(str(key), token.start())
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, str):
            next(tokens)

    visit(payload)
    return lines


def program_path_kind(line_text: str) -> str:
    if re.search(r"\b(?:rm|unlink|rmdir)\s*\(|\b(?:remove|delete)\s*\(", line_text):
        return "program_cleanup_path"
    if re.search(r"(?i)(?:facts\.json|investigation\.md|result\.json)", line_text):
        return "program_output_path"
    if re.search(r"\b(?:writeFile|writeFileSync|saveAs|screenshot|page\.pdf|mkdir|copyFile)\b", line_text):
        return "program_output_path"
    if re.search(r"(?i)\b(?:output|destination|dest|artifact|report)[\w]*\s*[:=]", line_text):
        return "program_output_path"
    return "program_path"


def program_output_variables(text: str) -> set[str]:
    """Find URL variables later passed to file-writing APIs in review scripts."""
    assignments = re.compile(
        r"(?:const|let|var)\s+([\w$]+)\s*=\s*(?:fileURLToPath\(\s*)?new\s+URL\("
    )
    output_call = re.compile(r"\b(?:writeFile\w*|saveAs|screenshot|copyFile|page\.pdf)\s*\(")
    output_positions = [match.start() for match in output_call.finditer(text)]
    variables: set[str] = set()
    for match in assignments.finditer(text):
        name = match.group(1)
        for start in output_positions:
            tail = text[start : text.find("\n", start) if "\n" in text[start:] else len(text)]
            if re.search(rf"\b{re.escape(name)}\b", tail):
                variables.add(name)
                break
    return variables


def program_output_assignment(text: str, line_number: int, output_variables: set[str]) -> bool:
    lines = text.splitlines()
    if not 1 <= line_number <= len(lines):
        return False
    match = re.search(r"(?:const|let|var)\s+([\w$]+)\s*=", lines[line_number - 1])
    return bool(match and match.group(1) in output_variables)


def program_resolution_base(source_path: str, text: str, line_number: int, target: str) -> str | None:
    """Resolve simple new URL(path, baseVar) references without guessing other runtime roots."""
    lines = text.splitlines()
    if line_number > len(lines):
        return None
    current_dir = posixpath.dirname(source_path)
    base_variables: dict[str, str] = {}
    declaration = re.compile(
        r"(?:const|let|var)\s+([\w$]+)\s*=\s*new\s+URL\(\s*(['\"])(.*?)\2\s*,\s*([\w$.]+)\s*,?\s*\)",
    )
    alias = re.compile(r"(?:const|let|var)\s+([\w$]+)\s*=\s*([\w$]+)\s*;")

    def collect_url_bases(module_text: str, module_dir: str) -> dict[str, str]:
        variables: dict[str, str] = {}
        declarations = list(declaration.finditer(module_text))
        aliases = list(alias.finditer(module_text))
        for _ in range(len(declarations) + len(aliases) + 1):
            changed = False
            for match in declarations:
                name, relative_ref, base_name = match.group(1), match.group(3), match.group(4)
                if base_name == "import.meta.url":
                    resolved = posixpath.normpath(posixpath.join(module_dir, unquote(relative_ref)))
                elif base_name in variables:
                    resolved = posixpath.normpath(posixpath.join(variables[base_name], unquote(relative_ref)))
                else:
                    continue
                if variables.get(name) != resolved:
                    variables[name] = resolved
                    changed = True
            for match in aliases:
                name, source_name = match.group(1), match.group(2)
                if source_name in variables and variables.get(name) != variables[source_name]:
                    variables[name] = variables[source_name]
                    changed = True
            if not changed:
                break
        return variables

    base_variables.update(collect_url_bases(text, current_dir))

    # Some legacy generation scripts import a base URL from document-source.mjs.
    for imported in re.finditer(r"import\s*\{([^}]+)\}\s*from\s*['\"]([^'\"]+)['\"]", text):
        module_ref = imported.group(2)
        module_path = posixpath.normpath(posixpath.join(current_dir, unquote(module_ref)))
        module_file = ROOT / module_path
        if not module_file.is_file():
            continue
        module_text = module_file.read_text(encoding="utf-8", errors="replace")
        module_dir = posixpath.dirname(module_path)
        base_variables.update(collect_url_bases(module_text, module_dir))

    line = lines[line_number - 1]
    url_call = re.search(
        r"new\s+URL\(\s*(['\"])(.*?)\1\s*,\s*([\w$.]+)\s*\)", line
    )
    if not url_call or url_call.group(2) != target:
        return None
    base_name = url_call.group(3)
    return base_variables.get(base_name)


@lru_cache(maxsize=None)
def anchors_for(path: str) -> set[str]:
    file_path = ROOT / path
    if not file_path.is_file() or extension(path) not in {".html", ".htm", ".md", ".svg"}:
        return set()
    text = file_path.read_text(encoding="utf-8", errors="replace")
    anchors = set(ANCHOR_RE.findall(text))
    if extension(path) == ".md":
        for heading in HEADING_RE.findall(text):
            slug = re.sub(r"[^\w\- ]", "", heading, flags=re.UNICODE).strip().lower()
            anchors.add(re.sub(r"\s+", "-", slug))
    return anchors


def safe_resolution(
    source_path: str,
    kind: str,
    target: str,
    current_by_path: dict[str, dict[str, str]],
    planned_by_path: dict[str, dict[str, str]],
    control_by_path: dict[str, str],
    resolution_base: str | None = None,
) -> dict[str, object]:
    target = target.strip().strip("<>")
    if not target:
        return {"status": "empty_target", "reason_code": "empty"}
    normalized_target = target.replace("\\", "/")
    parsed = urlsplit(normalized_target)
    if kind in {"json_reference_range", "json_reference_group"}:
        return {"status": "reference_group", "reason_code": "textual_path_range"}
    if kind == "program_output_path":
        return {"status": "generated_output", "reason_code": "program_writes_artifact"}
    if kind == "program_cleanup_path":
        return {"status": "cleanup_target", "reason_code": "program_removes_legacy_artifact"}
    if re.match(r"^[A-Za-z]:/", normalized_target) or normalized_target.startswith("//"):
        return {"status": "external_absolute", "reason_code": "absolute_path_from_other_environment"}
    if parsed.scheme.lower() == "node":
        return {"status": "external_dependency", "reason_code": "node_builtin_module"}
    if parsed.scheme.lower() in {"http", "https", "mailto", "tel", "ftp", "javascript", "data"} or target.startswith("//"):
        status = "embedded" if parsed.scheme.lower() == "data" else "external"
        return {"status": status, "reason_code": parsed.scheme.lower() or "protocol_relative"}
    if kind.startswith("program_") and not looks_like_path(normalized_target):
        return {"status": "external_dependency", "reason_code": "module_or_package_specifier"}
    if not parsed.path and parsed.fragment:
        return {
            "status": "anchor_resolved" if parsed.fragment in anchors_for(source_path) else "anchor_unverified",
            "reason_code": "same_document_fragment",
        }

    decoded = unquote(parsed.path).replace("\\", "/")
    if (
        source_path.startswith("tests_inago/")
        and kind == "program_path"
        and decoded in {
            "product-ledger.json",
            "post-status.json",
            "drafts.json",
            "review-target.json",
        }
    ):
        return {"status": "generated_output", "reason_code": "test_temporary_runtime_file"}
    if (
        decoded.startswith("/")
        and (kind.startswith("json_") or kind.startswith("program_"))
        and not decoded.lstrip("/").startswith(ROOT_RELATIVE_PREFIXES)
    ):
        return {"status": "external_absolute", "reason_code": "absolute_path_from_other_environment"}
    if (
        kind.startswith("program_")
        and source_path.startswith("tests_inago/")
        and decoded.startswith("./")
        and decoded[2:].startswith(("automation/", "rakuten-room-inago/"))
    ):
        candidate = posixpath.normpath(decoded[2:])
    elif decoded.startswith("/"):
        candidate = posixpath.normpath(decoded.lstrip("/"))
    elif (
        kind.startswith("program_") or kind.startswith("json_")
    ) and decoded.startswith(ROOT_RELATIVE_PREFIXES):
        candidate = posixpath.normpath(decoded)
    else:
        candidate = posixpath.normpath(
            posixpath.join(resolution_base or posixpath.dirname(source_path), decoded)
        )
        if (
            kind.startswith("program_")
            and resolution_base is None
            and not decoded.startswith(("./", "../", "/"))
            and candidate not in current_by_path
            and not (ROOT / candidate).exists()
        ):
            ancestor = posixpath.dirname(source_path)
            while ancestor and ancestor != ".":
                ancestor = posixpath.dirname(ancestor)
                candidate_at_ancestor = posixpath.normpath(posixpath.join(ancestor, decoded))
                if candidate_at_ancestor in current_by_path or (ROOT / candidate_at_ancestor).exists():
                    candidate = candidate_at_ancestor
                    break
    if candidate == ".." or candidate.startswith("../") or candidate.startswith("/"):
        return {"status": "unsafe_path", "reason_code": "escapes_repository"}
    if candidate.startswith("非公開作業データ/"):
        return {
            "status": "resolved_private" if (ROOT / candidate).exists() else "private_unavailable",
            "reason_code": "private_evidence_reference",
        }

    record = current_by_path.get(candidate)
    if record:
        result: dict[str, object] = {
            "status": "resolved_current",
            "reason_code": "indexed_current_path",
            "target_document_id": record["document_id"],
        }
        if record["visibility"] == "local_only":
            result["status"] = "resolved_non_public"
        if parsed.fragment:
            if record["visibility"] == "local_only":
                result["reason_code"] = "target_fragment_non_public"
            else:
                result["status"] = (
                    "resolved_current_anchor"
                    if parsed.fragment in anchors_for(candidate)
                    else "anchor_unverified"
                )
                result["reason_code"] = "target_fragment"
        return result

    future = planned_by_path.get(candidate)
    if future:
        return {
            "status": "planned_future",
            "reason_code": "planned_destination",
            "target_document_id": future["document_id"],
        }
    if candidate in control_by_path:
        return {
            "status": "resolved_control",
            "reason_code": "indexed_control_source",
            "target_document_id": control_by_path[candidate],
        }
    if (ROOT / candidate).is_dir():
        return {"status": "resolved_directory", "reason_code": "existing_directory"}
    if (ROOT / candidate).is_file():
        return {"status": "resolved_unindexed", "reason_code": "existing_repository_file"}
    return {
        "status": "unresolved_current",
        "reason_code": "path_not_present_or_indexed",
    }


def project_reference(
    source_path: str,
    kind: str,
    target: str,
    source_record: dict[str, str] | None,
    target_record: dict[str, str] | None,
    resolution: dict[str, object],
    resolution_base: str | None,
) -> str | None:
    parsed = urlsplit(target)
    status = str(resolution["status"])
    if kind == "embedded_document":
        return None
    if kind in {"json_reference_range", "json_reference_group", "program_output_path", "program_cleanup_path"}:
        return None
    if status in {
        "unresolved_current", "historical_unresolved", "unsafe_path", "private_unavailable",
        "resolved_private", "external_absolute", "external", "embedded", "external_dependency",
        "anchor_unverified", "reference_group", "generated_output", "cleanup_target", "resolved_directory",
        "resolved_non_public",
    }:
        return None
    if parsed.scheme or target.startswith("//"):
        return None
    if not parsed.path and parsed.fragment:
        return target
    if (
        parsed.path.startswith("/")
        and (kind.startswith("json_") or kind.startswith("program_"))
        and not parsed.path.lstrip("/").startswith(ROOT_RELATIVE_PREFIXES)
    ):
        return None
    if (
        kind.startswith("program_") or kind.startswith("json_")
    ) and target.startswith(ROOT_RELATIVE_PREFIXES):
        return target_record["planned_destination"] if target_record else target
    source_dest = (
        source_record["planned_destination"]
        if source_record and source_record["planned_destination"]
        else source_path
    )
    target_dest = target_record["planned_destination"] if target_record else None
    decoded_target = unquote(parsed.path).replace("\\", "/")
    if decoded_target.startswith("/") and target_record is None:
        target_dest = decoded_target.lstrip("/")
    elif target_record is None and status in {"resolved_unindexed", "resolved_control"}:
        target_dest = posixpath.normpath(posixpath.join(posixpath.dirname(source_path), decoded_target))
    if target_dest is None:
        return None
    if resolution_base and resolution_base != posixpath.dirname(source_path):
        return None
    rel = posixpath.relpath(target_dest, posixpath.dirname(source_dest) or ".")
    projected = quote(rel, safe="/-._~")
    if parsed.query:
        projected += "?" + parsed.query
    if parsed.fragment:
        projected += "#" + parsed.fragment
    return projected


def resolve_document_id(document_id: str, records_by_id: dict[str, dict[str, str]]) -> dict[str, str]:
    record = records_by_id.get(document_id)
    if record is None:
        raise KeyError(f"unknown_document_id:{document_id}")
    destination = record["planned_destination"]
    path = PurePosixPath(destination)
    if (
        not destination
        or path.is_absolute()
        or ".." in path.parts
        or "\\" in destination
        or "\x00" in destination
        or re.match(r"^[A-Za-z]:", destination)
        or not destination.startswith(ROOT_RELATIVE_PREFIXES)
    ):
        raise ValueError(f"unsafe_index_destination:{document_id}")
    return {"document_id": document_id, "path": destination, "status": record["lifecycle_class"]}


def main() -> int:
    manifest = read_manifest()
    validate_manifest_paths(manifest)
    docs = [row for row in manifest if row["lifecycle_class"] != "future"]
    future = [row for row in manifest if row["lifecycle_class"] == "future"]
    if future:
        raise ValueError("U04 must promote all planned README entries before indexing")
    if len(docs) != 405:
        raise ValueError(f"Expected 405 current documents after U04, got {len(docs)}")
    if any(row["current_path"] != row["planned_destination"] for row in docs):
        raise ValueError("U04 manifest current_path must equal planned_destination after migration")

    catalog = read_catalog()
    proposal_path = relative_path(PROPOSAL.relative_to(ROOT).as_posix())
    u02_source_paths = {relative_path(row[1]) for row in U02_ADDITIONS}
    expected_original_sources = set(catalog) | {proposal_path} | u02_source_paths
    original_rows = [row for row in docs if row["added_in"] in {"U01", "U02"}]
    actual_original_sources = {row["source_path"] for row in original_rows}
    if actual_original_sources != expected_original_sources:
        raise ValueError("Original U01/A/U02 source inventory differs from the approved manifest")
    if len([row for row in original_rows if row["added_in"] == "U01"]) != 394:
        raise ValueError("U01 source inventory must remain 394 documents")
    if len([row for row in original_rows if row["added_in"] == "U02"]) != 3:
        raise ValueError("U02 source inventory must remain three documents")
    if len([row for row in docs if row["added_in"] == "U04"]) != 8:
        raise ValueError("U04 must contain the eight approved README entry points")

    by_source = {row["source_path"]: row for row in original_rows if row["source_path"]}
    for row in original_rows:
        if row["added_in"] == "U02":
            expected = next(dest for doc_id, source, dest, _role in U02_ADDITIONS if doc_id == row["document_id"])
        elif row["source_path"] == proposal_path:
            expected = proposal_path
        else:
            expected = relative_path(catalog[row["source_path"]]["destination"])
        if row["planned_destination"] != expected:
            raise ValueError(f"Approved destination changed for {row['document_id']}")
    added_ids = {row["document_id"] for row in docs if row["added_in"] == "U04"}
    expected_added_ids = {"FUTURE-README-001", *(f"DOC-{i:04d}" for i in range(398, 405))}
    if added_ids != expected_added_ids:
        raise ValueError("U04 README document IDs differ from the approved stable set")
    for row in docs:
        if row["added_in"] == "U04" and (row["source_path"] or row["current_path"] != row["planned_destination"]):
            raise ValueError(f"Invalid U04-created document row: {row['document_id']}")

    ids = [row["document_id"] for row in docs]
    current_paths = [row["current_path"] for row in docs]
    target_paths = [row["planned_destination"] for row in docs]
    if len(ids) != len(set(ids)) or len(current_paths) != len(set(current_paths)):
        raise ValueError("Document IDs or current paths collide")
    if len(target_paths) != len(set(target_paths)):
        raise ValueError("Planned destinations collide")
    repo_rows = [row for row in docs if row["visibility"] == "repository"]
    local_rows = [row for row in docs if row["visibility"] == "local_only"]
    if len(local_rows) != 3 or len(repo_rows) != 402:
        raise ValueError("Repository/local-only inventory counts changed")
    missing_repo = [row["document_id"] for row in repo_rows if not (ROOT / row["current_path"]).is_file()]
    if missing_repo:
        raise FileNotFoundError(f"Repository destinations missing for {len(missing_repo)} document IDs")

    # Local-only rows are resolved in the manifest but need not be present in this managed worktree.
    current_by_path = {row["current_path"]: row for row in docs}
    planned_by_path = {row["planned_destination"]: row for row in docs}
    records_by_id = {row["document_id"]: row for row in docs}

    control_sources = list(CONTROL_SOURCES)
    known_control_paths = {relative_path(path) for _doc_id, path in control_sources}
    for root_name, source_prefix in (("automation/inago", "SRC-AUTOMATION"), ("tests_inago", "SRC-TESTS")):
        control_root = ROOT / root_name
        for path in sorted(control_root.rglob("*.py")):
            rel = relative_path(path.relative_to(ROOT).as_posix())
            if rel in known_control_paths:
                continue
            source_id = source_prefix + "-" + re.sub(r"[^A-Za-z0-9]+", "-", rel).strip("-").upper()
            control_sources.append((source_id, rel))
            known_control_paths.add(rel)
    control_ids = [doc_id for doc_id, _path in control_sources]
    if len(control_ids) != len(set(control_ids)):
        raise ValueError("Control source IDs collide")
    control_by_path = {relative_path(path): doc_id for doc_id, path in control_sources}

    q_row = next(row for row in docs if row["document_id"] == "DOC-0370")
    q_html = (ROOT / q_row["current_path"]).read_text(encoding="utf-8")
    q_match = re.search(
        r'<script id="requirements-source" type="application/json">([\s\S]*?)</script>',
        q_html,
    )
    if not q_match:
        raise ValueError("Canonical Q HTML has no requirements-source snapshot")
    q_data = json.loads(q_match.group(1))
    q_attachment_ids = q_data.get("referenceDocumentIds", {})
    q_references = q_data.get("references", {})
    if not isinstance(q_attachment_ids, dict) or not isinstance(q_references, dict):
        raise ValueError("Canonical Q attachment map is invalid")

    all_scan_sources: list[tuple[str, str, dict[str, str] | None]] = []
    for row in docs:
        path = ROOT / row["current_path"]
        if row["visibility"] == "repository" or path.is_file():
            all_scan_sources.append((row["document_id"], row["current_path"], row))
    all_scan_sources.extend(
        (doc_id, path, None)
        for doc_id, path in control_sources
        if (ROOT / path).is_file() and path not in current_by_path
    )

    references: list[dict[str, object]] = []
    private_references: list[dict[str, object]] = []
    counts_by_doc: Counter[str] = Counter()
    for source_id, source_path, source_record in all_scan_sources:
        file_path = ROOT / source_path
        if extension(source_path) not in TEXT_EXTENSIONS:
            continue
        try:
            source_text = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for item in extract_references(source_path, source_text):
            kind = str(item["kind"])
            target = str(item["target"])
            line = int(item["line"])
            resolution_base = None
            if kind.startswith("program_") and kind not in {"program_output_path", "program_cleanup_path"}:
                resolution_base = program_resolution_base(source_path, source_text, line, target)

            if kind == "embedded_document" and source_id == q_row["document_id"]:
                target_id = q_attachment_ids.get(target)
                if target_id in records_by_id:
                    resolved: dict[str, object] = {
                        "status": "resolved_current",
                        "reason_code": "stable_attachment_document_id",
                        "target_document_id": target_id,
                    }
                else:
                    resolved = {"status": "unresolved_current", "reason_code": "attachment_id_missing"}
            else:
                resolved = safe_resolution(
                    source_path,
                    kind,
                    target,
                    current_by_path,
                    planned_by_path,
                    control_by_path,
                    resolution_base,
                )
                exception_key = (source_id, kind, unquote(urlsplit(target).path))
                if exception_key in HISTORICAL_PATH_EXCEPTIONS:
                    resolved = {
                        "status": "historical_unresolved",
                        "reason_code": "historical_execution_path_preserved",
                    }
            target_id = resolved.get("target_document_id")
            target_record = records_by_id.get(str(target_id)) if target_id else None
            status = str(resolved["status"])
            if source_record and source_record["lifecycle_class"] == "historical" and status == "unresolved_current":
                status = "historical_unresolved"
            projected = project_reference(
                source_path,
                kind,
                target,
                source_record,
                target_record,
                resolved,
                resolution_base,
            )
            projection_status = (
                "projected"
                if projected is not None
                else "generated_output_requires_rebase"
                if resolved["status"] == "generated_output"
                else "cleanup_target_requires_review"
                if resolved["status"] == "cleanup_target"
                else "directory_rebase_required"
                if resolved["status"] == "resolved_directory"
                else "manual_base_rebase_required"
                if resolution_base and resolution_base != posixpath.dirname(source_path)
                else "follow_up_required"
                if resolved["status"] in {"unresolved_current", "historical_unresolved", "anchor_unverified", "private_unavailable"}
                else "not_rewritten"
            )
            record = {
                "source_id": source_id,
                "source_path": source_path,
                "line": line,
                "kind": kind,
                "status": status,
                "reason_code": resolved["reason_code"],
                "target_document_id": target_id,
                "projected_reference": projected,
                "projection_status": projection_status,
                "percent_encoded": "%" in target,
                "query_present": bool(urlsplit(target).query),
            }
            references.append(record)
            private_references.append(
                {**record, "target_path": urlsplit(target).path, "resolution_base": resolution_base}
            )
            counts_by_doc[source_id] += 1

    source_by_id = {row["document_id"]: row for row in docs}
    projection_errors = []
    for row in references:
        projected = row["projected_reference"]
        target_id = row["target_document_id"]
        if not projected or not target_id or target_id not in records_by_id:
            continue
        target_record = records_by_id[str(target_id)]
        source_record = source_by_id.get(str(row["source_id"]))
        source_destination = (
            source_record["planned_destination"]
            if source_record and source_record["planned_destination"]
            else str(row["source_path"])
        )
        parsed_projection = urlsplit(str(projected))
        if (
            str(row["kind"]).startswith(("json_", "program_"))
            and unquote(parsed_projection.path).lstrip("/").startswith(ROOT_RELATIVE_PREFIXES)
        ):
            projected_target = unquote(parsed_projection.path)
        elif parsed_projection.path.startswith("/"):
            projected_target = unquote(parsed_projection.path).lstrip("/")
        else:
            projected_target = posixpath.normpath(
                posixpath.join(posixpath.dirname(source_destination), unquote(parsed_projection.path))
            )
        if projected_target != target_record["planned_destination"]:
            projection_errors.append(
                {
                    "source_id": row["source_id"],
                    "line": row["line"],
                    "target_document_id": target_id,
                    "projected_reference": projected,
                    "resolved_projected_path": projected_target,
                    "expected_planned_destination": target_record["planned_destination"],
                }
            )
    if projection_errors:
        raise ValueError(f"Projected reference mismatch: {projection_errors[:5]}")

    public_references = [
        {
            "source_id": row["source_id"],
            "line": row["line"],
            "kind": row["kind"],
            "status": row["status"],
            "reason_code": row["reason_code"],
            "target_document_id": row["target_document_id"],
            "projected_reference": row["projected_reference"],
            "projection_status": row["projection_status"],
            "percent_encoded": row["percent_encoded"],
            "query_present": row["query_present"],
        }
        for row in references
    ]

    legacy_sources_remaining = [
        row["document_id"]
        for row in docs
        if row["source_path"]
        and row["source_path"] != row["current_path"]
        and (ROOT / row["source_path"]).exists()
    ]
    if legacy_sources_remaining:
        raise ValueError(f"Old repository sources remain for {len(legacy_sources_remaining)} document IDs")

    status_counts = Counter(str(row["status"]) for row in references)
    role_counts = Counter(row["role"] for row in docs)
    lifecycle_counts = Counter(row["lifecycle_class"] for row in docs)
    visibility_counts = Counter(row["visibility"] for row in docs)
    percent_path_records = [
        {
            "document_id": row["document_id"],
            "current_path": row["current_path"],
            "planned_destination": row["planned_destination"],
            "one_decode_exists": (ROOT / unquote(row["current_path"])).is_file(),
            "literal_path_exists": (ROOT / row["current_path"]).is_file(),
            "url_literal_encoding": "encode each % as %25 before encoding UTF-8 path bytes",
        }
        for row in docs
        if "%" in row["current_path"]
    ]
    if len(percent_path_records) != 1 or percent_path_records[0]["one_decode_exists"]:
        raise ValueError("The literal percent-character path must remain distinct from a decoded path")

    known_id_result = resolve_document_id("DOC-0395", records_by_id)
    legacy_id_result = resolve_document_id("DOC-0390", records_by_id)
    docs_readme_result = resolve_document_id("FUTURE-README-001", records_by_id)
    valid_destinations: list[str] = []
    invalid_destinations: list[dict[str, str]] = []
    for row in manifest:
        try:
            result = resolve_document_id(row["document_id"], records_by_id)
            valid_destinations.append(result["path"])
        except (KeyError, ValueError) as exc:
            invalid_destinations.append({"document_id": row["document_id"], "error": str(exc)})
    try:
        resolve_document_id("DOC-9999", records_by_id)
        unknown_id_rejected = False
    except KeyError:
        unknown_id_rejected = True
    unsafe_destination_rejected = False
    try:
        resolve_document_id(
            "DOC-UNSAFE",
            {"DOC-UNSAFE": {"planned_destination": "../../outside/file.md", "lifecycle_class": "current"}},
        )
    except ValueError:
        unsafe_destination_rejected = True
    traversal_result = safe_resolution(
        "docs/README.md", "markdown_link", "../../outside/secret.md",
        current_by_path, planned_by_path, control_by_path,
    )
    encoded_traversal_result = safe_resolution(
        "docs/README.md", "markdown_link", "..%5C..%5Coutside%5Csecret.md",
        current_by_path, planned_by_path, control_by_path,
    )

    expected_repo_paths = {row["current_path"] for row in repo_rows}
    actual_repo_paths = {row["current_path"] for row in repo_rows if (ROOT / row["current_path"]).is_file()}
    proposal_in_manifest = any(row["current_path"] == proposal_path for row in docs)
    source_set_matches = actual_original_sources == expected_original_sources
    c01_cases = {
        "u01_original_inventory": {
            "expected": 394,
            "actual": len([row for row in original_rows if row["added_in"] == "U01"]),
            "status": "PASS" if len([row for row in original_rows if row["added_in"] == "U01"]) == 394 else "FAIL",
        },
        "a_inventory_plus_proposal": {
            "expected_a_entries": 393,
            "actual_a_entries": len(catalog),
            "proposal_document_present": proposal_in_manifest,
            "status": "PASS" if len(catalog) == 393 and proposal_in_manifest else "FAIL",
        },
        "u02_increment": {
            "expected": 3,
            "actual": len([row for row in docs if row["added_in"] == "U02"]),
            "status": "PASS" if len([row for row in docs if row["added_in"] == "U02"]) == 3 else "FAIL",
        },
        "u04_readme_entries": {
            "expected": 8,
            "actual": len([row for row in docs if row["added_in"] == "U04"]),
            "future_docs_readme_current": docs_readme_result["status"] == "current",
            "status": "PASS" if len([row for row in docs if row["added_in"] == "U04"]) == 8 and docs_readme_result["status"] == "current" else "FAIL",
        },
        "current_document_set": {
            "expected_documents": 405,
            "actual_documents": len(docs),
            "repository_files_expected": len(expected_repo_paths),
            "repository_files_present": len(actual_repo_paths),
            "repository_file_set_matches": expected_repo_paths == actual_repo_paths,
            "local_only_documents": len(local_rows),
            "original_source_inventory_matches": source_set_matches,
            "old_repository_sources_remaining": len(legacy_sources_remaining),
            "status": "PASS" if len(docs) == 405 and expected_repo_paths == actual_repo_paths and len(local_rows) == 3 and source_set_matches and not legacy_sources_remaining else "FAIL",
        },
        "unique_ids_and_destinations": {
            "unique_ids": len(set(ids)),
            "all_ids": len(ids),
            "unique_destinations": len(set(target_paths)),
            "all_destinations": len(target_paths),
            "status": "PASS" if len(set(ids)) == len(ids) and len(set(target_paths)) == len(target_paths) else "FAIL",
        },
    }
    c01_status = "PASS" if all(case["status"] == "PASS" for case in c01_cases.values()) else "FAIL"

    q_embedded_references = [
        row for row in private_references
        if row["source_id"] == q_row["document_id"] and row["kind"] == "embedded_document"
    ]
    q_embedded_mapped = [row for row in q_embedded_references if row["target_document_id"] in records_by_id]
    attachment_checks = []
    if set(q_references) != set(q_attachment_ids):
        attachment_checks.append({"status": "FAIL", "reason": "attachment_keys_do_not_match_id_map"})
    attachment_id_values = list(q_attachment_ids.values())
    q_attachment_ids_unique = (
        len(q_attachment_ids) == 26
        and all(isinstance(document_id, str) for document_id in attachment_id_values)
        and len(set(attachment_id_values)) == 26
    )
    if not q_attachment_ids_unique:
        attachment_checks.append({"status": "FAIL", "reason": "attachment_document_ids_not_unique"})
    for name, doc_id in q_attachment_ids.items():
        row = records_by_id.get(doc_id)
        if row is None or row["visibility"] != "repository":
            attachment_checks.append({"status": "FAIL", "reason": "attachment_document_id_invalid"})
            continue
        try:
            encoded_matches = base64.b64decode(q_references[name], validate=True) == (ROOT / row["current_path"]).read_bytes()
        except (KeyError, OSError, ValueError):
            encoded_matches = False
        attachment_checks.append({"status": "PASS" if encoded_matches else "FAIL", "document_id": doc_id})
    q_attachments_status = (
        "PASS"
        if len(q_embedded_references) == 26
        and len(q_embedded_mapped) == 26
        and q_attachment_ids_unique
        and len(attachment_checks) == 26
        and all(check["status"] == "PASS" for check in attachment_checks)
        else "FAIL"
    )

    json_occurrences: dict[tuple[str, str, str], list[int]] = {}
    json_occurrence_ids: dict[tuple[str, str, str], str] = {}
    for row in private_references:
        if extension(str(row["source_path"])) != ".json" or not str(row["kind"]).startswith("json_"):
            continue
        key = (str(row["source_path"]), str(row["kind"]), str(row["target_path"]))
        json_occurrences.setdefault(key, []).append(int(row["line"]))
        json_occurrence_ids[key] = str(row["source_id"])
    repeated_json_occurrences = [
        {"source_id": json_occurrence_ids[key], "source_path": key[0], "kind": key[1], "target_path": key[2], "lines": sorted(lines)}
        for key, lines in json_occurrences.items()
        if len(set(lines)) > 1
    ]
    c02_allowed_statuses = {
        "anchor_resolved", "anchor_unverified", "cleanup_target", "embedded", "external",
        "external_absolute", "external_dependency", "generated_output", "historical_unresolved",
        "private_unavailable", "reference_group", "resolved_control", "resolved_current",
        "resolved_current_anchor", "resolved_directory", "resolved_non_public", "resolved_private",
        "resolved_unindexed", "unresolved_current", "unsafe_path", "empty_target", "planned_future",
    }
    unclassified = [row for row in references if str(row["status"]) not in c02_allowed_statuses]
    active_unresolved = [row for row in private_references if row["status"] == "unresolved_current"]
    c02_status = (
        "PASS"
        if not unclassified and not active_unresolved and len(repeated_json_occurrences) > 0 and q_attachments_status == "PASS"
        else "FAIL"
    )
    c02 = {
        "status": c02_status,
        "references_extracted": len(references),
        "q_embedded_markdown_references": len(q_embedded_references),
        "q_embedded_markdown_mapped_to_document_ids": len(q_embedded_mapped),
        "q_embedded_content_matches_current_documents": q_attachments_status,
        "json_same_target_occurrences_with_distinct_lines": len(repeated_json_occurrences),
        "status_counts": dict(sorted(status_counts.items())),
        "unclassified_count": len(unclassified),
        "current_unresolved_count": len(active_unresolved),
        "current_unresolved": [
            {"source_id": row["source_id"], "source_path": row["source_path"], "line": row["line"], "kind": row["kind"], "target_path": row["target_path"]}
            for row in active_unresolved
        ],
        "q_attachment_checks": attachment_checks,
    }

    c05_cases = {
        "known_u02_id": {
            "input": "DOC-0395",
            "expected": "docs/specifications/inago/AT-MAINT-01_保守対応要件受入契約.md",
            "actual": known_id_result["path"],
            "status": "PASS" if known_id_result["path"] == "docs/specifications/inago/AT-MAINT-01_保守対応要件受入契約.md" else "FAIL",
        },
        "known_existing_id": {
            "input": "DOC-0390",
            "expected": "docs/specifications/inago/要件追跡表.md",
            "actual": legacy_id_result["path"],
            "status": "PASS" if legacy_id_result["path"] == "docs/specifications/inago/要件追跡表.md" else "FAIL",
        },
        "promoted_docs_readme_id": {
            "input": "FUTURE-README-001",
            "expected": "docs/README.md",
            "actual": docs_readme_result["path"],
            "status": "PASS" if docs_readme_result["path"] == "docs/README.md" and docs_readme_result["status"] == "current" else "FAIL",
        },
        "unknown_id_rejected": {"status": "PASS" if unknown_id_rejected else "FAIL"},
        "root_escape_rejected": {"status": "PASS" if traversal_result["status"] == "unsafe_path" else "FAIL"},
        "encoded_separator_root_escape_rejected": {"status": "PASS" if encoded_traversal_result["status"] == "unsafe_path" else "FAIL"},
        "unsafe_destination_rejected": {"status": "PASS" if unsafe_destination_rejected else "FAIL"},
        "all_destinations_safe": {
            "expected": len(manifest),
            "actual": len(valid_destinations),
            "invalid_count": len(invalid_destinations),
            "status": "PASS" if len(valid_destinations) == len(manifest) and not invalid_destinations else "FAIL",
        },
    }
    c05_status = "PASS" if all(case["status"] == "PASS" for case in c05_cases.values()) else "FAIL"

    for row in docs:
        row["extension"] = extension(row["current_path"])
        current_file = ROOT / row["current_path"]
        if current_file.is_file():
            row["bytes"] = current_file.stat().st_size
        else:
            old_index = json.loads(INDEX.read_text(encoding="utf-8")) if INDEX.is_file() else {"documents": []}
            old_doc = next((item for item in old_index["documents"] if item["document_id"] == row["document_id"]), None)
            row["bytes"] = old_doc.get("bytes", 0) if old_doc else 0
        row["reference_count"] = counts_by_doc[row["document_id"]]

    index = {
        "schema_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "index_path": "docs/document-index.json",
        "manifest_path": "worklogs/projects/maintenance/移行manifest.csv",
        "reference_ledger_path": "worklogs/projects/maintenance/移行・参照台帳.md",
        "source_inventory": {
            "u01_baseline": 394,
            "a_proposal_entries": len(catalog),
            "u02_increment": 3,
            "u04_readme_entries": 8,
            "indexed_current_documents": len(docs),
            "future_documents": 0,
            "generated_index_is_self_excluded": True,
            "roles": dict(sorted(role_counts.items())),
            "lifecycle_classes": dict(sorted(lifecycle_counts.items())),
            "visibility": dict(sorted(visibility_counts.items())),
        },
        "documents": docs,
        "future_documents": [],
        "control_sources": [{"source_id": doc_id, "path": path} for doc_id, path in control_sources],
        "references": public_references,
        "reference_summary": {
            "count": len(references),
            "status_counts": dict(sorted(status_counts.items())),
            "occupied_destination_dependencies": [],
            "percent_encoded_literal_paths": percent_path_records,
        },
        "mapping_rules": [
            "Stable document IDs and original source_path values are preserved; current_path follows the completed U04 destinations.",
            "Filesystem source paths are literal POSIX-relative paths. Percent characters in a source filename are not decoded.",
            "URL paths are percent-decoded exactly once for resolution; literal percent characters in a URL must be encoded as %25.",
            "Relative links resolve from current_path; known references are checked against document IDs and current destinations.",
            "Program output and cleanup paths remain distinct from input links and are rebased in their owning generator.",
            "Historical JSON evidence and prose retain their original path facts; only navigation links are updated in historical documents.",
            "The former future docs/README.md ID is current, and U04 adds seven additional README entry points.",
            "The canonical requirements HTML refreshes embedded Markdown by referenceDocumentIds and document-index current_path; old Markdown is never imported.",
            "Four references in DOC-0059 are preserved historical execution paths and are classified as historical exceptions, not current inputs.",
        ],
    }
    INDEX.parent.mkdir(parents=True, exist_ok=True)
    INDEX.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    PRIVATE_EVIDENCE.mkdir(parents=True, exist_ok=True)
    (PRIVATE_EVIDENCE / "reference-detail.json").write_text(
        json.dumps(private_references, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    summary = {
        "status": "PASS" if c01_status == c02_status == c05_status == "PASS" else "FAIL",
        "cases": {"C01": {"status": c01_status, "checks": c01_cases}, "C02": c02, "C05": {"status": c05_status, "checks": c05_cases}},
        "baseline_count": 394,
        "proposal_count": len(catalog),
        "u02_increment": 3,
        "u04_readmes": 8,
        "indexed_documents": len(docs),
        "repository_documents": len(repo_rows),
        "local_only_documents": len(local_rows),
        "future_documents": 0,
        "reference_count": len(references),
        "reference_status_counts": dict(sorted(status_counts.items())),
        "unclassified_count": len(unclassified),
        "current_unresolved_count": len(active_unresolved),
        "q_embedded_documents": len(q_embedded_mapped),
        "q_embedded_content_matches": q_attachments_status,
        "old_repository_sources_remaining": len(legacy_sources_remaining),
        "collect_or_pytest": "not run; U04 uses static inventory/reference checks",
    }
    (PRIVATE_EVIDENCE / "static-validation.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(json.dumps({key: value for key, value in summary.items() if key != "reference_status_counts"} | {"reference_status_counts": dict(sorted(status_counts.items()))}, ensure_ascii=False, indent=2))
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
