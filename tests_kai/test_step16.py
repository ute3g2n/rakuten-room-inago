"""Release audit regressions for secret scan and pre-cutover packaging."""

from automation.secret_scan import scan_paths


def test_secret_scan_distinguishes_variable_references_from_literal_values(tmp_path):
    variables = tmp_path / "variables.html"
    variables.write_text('apikey:$ANON\nAuthorization: Bearer $SEC\n'
                         'const head={apikey:anon, Authorization:`Bearer ${anon}`};\n', encoding="utf-8")
    assert scan_paths([variables]) == []

    literal = tmp_path / "literal.txt"
    literal.write_text('apiKey=dummy-sensitive-value\n', encoding="utf-8")
    assert len(scan_paths([literal])) == 1
