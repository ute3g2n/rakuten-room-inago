"""Scan newly generated artifacts for credential-shaped values without printing them."""

import re
from pathlib import Path


FORBIDDEN_NAMES = {"auth-state.json", "rakuten-api.credentials.json", "credentials.json"}
ASSIGNMENT = re.compile(
    r"(?i)(?:access[_-]?key|api[_-]?key|password|cookie|authorization|bearer)[\"']?\s*[=:]\s*[\"']?([^\s\"',;}{]+)"
)
TEXT_SUFFIXES = {".json", ".jsonl", ".html", ".log", ".txt", ".xml", ".md"}


def scan_paths(paths: list[Path]) -> list[str]:
    findings = []
    for base in paths:
        files = base.rglob("*") if base.is_dir() else [base]
        for path in files:
            if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            if path.name.lower() in FORBIDDEN_NAMES:
                findings.append(f"credential-named file: {path}")
                continue
            content = path.read_text(encoding="utf-8", errors="replace")
            for match in ASSIGNMENT.finditer(content):
                value = match.group(1).strip()
                remainder = content[match.end():].split("\n", 1)[0].lstrip()
                variable = (value.startswith("$")
                            or (value.casefold() == "bearer" and remainder.startswith("$"))
                            or (value.casefold() == "`bearer" and remainder.startswith("${"))
                            or (value == "anon" and path.suffix.lower() in {".js", ".html"}
                                and remainder.startswith((",", "}"))))
                if value and not variable and value.upper() not in {"[REDACTED]", "REDACTED", "NULL", "NONE"}:
                    findings.append(f"credential-shaped value: {path}")
                    break
    return findings


if __name__ == "__main__":
    import sys
    roots = [Path(arg) for arg in sys.argv[1:]]
    issues = scan_paths(roots)
    for issue in issues:
        print(issue)
    print(f"secret_scan: {len(issues)} finding(s)")
    raise SystemExit(1 if issues else 0)
