# STEP04 固定rootで移行を適用

## 実行記録

- 担当: STEP04 移行反映担当
- 作業root: `C:\project\rakuten-room-inago` のみ
- 対象runtime: `C:\project\rakuten-room-inago\rakuten-room-inago\runtime`。STEP05 promptの `--data-root` と一致する。
- 対象管理DB: `C:\project\rakuten-room-inago\rakuten-room-inago\runtime\maintenance\state.sqlite`。`MaintenanceStore` の解決規則は `<data-root>/maintenance/state.sqlite`。Daily DB `<data-root>/state.sqlite` は開かず、更新していない。
- 実行時刻: 2026-10-21 07:50 JSTごろに適用。サブ秒開始時刻はexec wrapperが収集しないため不明。終了後、2026-10-21 07:51 JSTに読取照合を完了。
- 対象コード: `automation/inago/maintenance/store.py` の `MaintenanceStore.initialize()` と migration 17〜19 (`sql/017_optional_release_anchor.sql`, `sql/018_optional_ai_role_config_hash.sql`, `sql/019_remove_legacy_maintenance_attestation_gates.sql`)。
- 設定ファイルは読取のみで確認し変更なし。Worker CLI、runner、Daily、Schedulerを起動・再起動していない。Git操作、別checkout、他runtimeの操作なし。

## 事前確認

### 実行中プロセス

事前のプロセス確認で保守CLIに該当するPythonプロセスは0件。広い文字列検索では確認コマンドを含むPowerShellが2件表示されたが、どちらも保守Pythonプロセスではなかった。移行中に別プロセスを起動・停止していない。事後の絞り込み確認も0件、exit 0。

```powershell
$matches = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -match 'automation\.inago\.maintenance|maintenance\.cli' } | Select-Object ProcessId,Name)
"matching_maintenance_python_processes=$($matches.Count)"
$matches | Format-Table -AutoSize
```

期待: 対象DBを利用中の保守workerがあれば、その実終了を確認する。実結果: `matching_maintenance_python_processes=0`、exit 0。よってSTEP04ではプロセス再起動を行っていない。

### DBの読み取り専用preflight

通常の初期化処理を呼ばず、指定runtimeの既存DBだけをSQLite `mode=ro` で照会した。対象ファイルが存在し、application_id=`1229866829`、user_version=`16`、migration記録が1〜16であることを確認。テーブルは34。以下の行件数を移行前記録として保存した。DBの行内容、秘密、個人情報は出力していない。

| table | rows | table | rows |
|---|---:|---|---:|
| daily_check | 3 | daily_launch | 2 |
| daily_result_observation | 2 | deployment | 17 |
| deployment_verification | 0 | handoff_artifact | 1 |
| incident | 1 | incident_run | 1 |
| job_input_manifest | 1 | lease | 36 |
| maintenance_ai_observation | 246 | maintenance_ai_session | 28 |
| maintenance_job | 1 | maintenance_job_resume_event | 30 |
| maintenance_operation_event | 549 | maintenance_workflow_stage_observation | 189 |
| release | 15 | release_gate | 12 |
| release_required_test | 38 | repair_campaign | 0 |
| repair_campaign_case | 0 | repair_campaign_result | 0 |
| repair_case_run_start | 0 | repair_host_review_execution | 0 |
| repair_review_receipt | 0 | repair_run_receipt | 0 |
| review_result | 42 | schema_migration | 16 |
| step_attempt | 63 | test_ledger | 38 |
| watchdog_cursor | 0 | watchdog_queue | 0 |
| worker_queue | 2 | worker_queue_event | 0 |

期待: 既存管理DBを特定し、初期化やDaily DB操作を避ける。実結果: version 16の既存管理DBを特定。現行schema versionは19のため、v19へ進めるにはこのDBで順次17、18、19が必要と確認した。preflight `PRAGMA quick_check` は `ok`、`PRAGMA foreign_key_check` は該当なし。DBへの書込みなし、Pythonコマンドexit 0。

実行したpreflightは、上記絶対パスを固定し `sqlite3.connect(db.as_uri() + '?mode=ro', uri=True)`、`PRAGMA query_only=ON` の接続でapplication_id/user_version/schema_migration/table row countを読む処理。`MaintenanceStore.initialize()`、CLI、migrationは呼ばなかった。

## Backupとmigration

### Backup

適用前にSQLite backup APIで以下の運用バックアップを新規作成した。

`C:\project\rakuten-room-inago\rakuten-room-inago\runtime\maintenance\backups\state-pre-v19-20261021T075007JST.sqlite`

backupはreadonly接続から作成し、backup側をreadonlyで再読込。application_id、user_version=16、34テーブルの全行件数、quick_check、foreign_key_checkが元DBと一致した。既存backupは上書きしていない。

### 実行コマンド

次のPowerShell here-stringを `.venv-inago/Scripts/python.exe -B -` に渡した。preconditionで既存DBのapplication_idとversion 16を確認し、backup作成・検証後に現行 `MaintenanceStore.initialize()` を呼んだ。これにより同じ管理DBに必要な17、18、19だけが順次適用された。接続は閉じ、適用後はmigration history、table counts、integrityを確認した。

```powershell
@'
from pathlib import Path
import sqlite3

expected_app_id = 1229866829
expected_version = 16
root = Path(r'C:\project\rakuten-room-inago\rakuten-room-inago\runtime').resolve(strict=True)
db = root / 'maintenance' / 'state.sqlite'
backup = root / 'maintenance' / 'backups' / 'state-pre-v19-20261021T075007JST.sqlite'
if not db.is_file() or db.is_symlink():
    raise SystemExit('target_management_db_missing_or_reparse')
if not backup.parent.is_dir() or backup.exists():
    raise SystemExit('backup_directory_missing_or_backup_exists')

def snapshot(path: Path, *, readonly: bool):
    uri = path.as_uri() + ('?mode=ro' if readonly else '')
    connection = sqlite3.connect(uri, uri=readonly, timeout=5.0)
    try:
        connection.execute('PRAGMA busy_timeout=5000')
        if readonly:
            connection.execute('PRAGMA query_only=ON')
        app_id = int(connection.execute('PRAGMA application_id').fetchone()[0])
        version = int(connection.execute('PRAGMA user_version').fetchone()[0])
        tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        counts = {}
        for name in tables:
            quoted = '"' + name.replace('"', '""') + '"'
            counts[name] = int(connection.execute(f'SELECT COUNT(*) FROM {quoted}').fetchone()[0])
        quick = connection.execute('PRAGMA quick_check').fetchone()[0]
        foreign_key_issues = connection.execute('PRAGMA foreign_key_check').fetchmany(1)
        return connection, app_id, version, counts, quick, foreign_key_issues
    except Exception:
        connection.close()
        raise

source, app_id, version, before_counts, quick, fk_issues = snapshot(db, readonly=True)
try:
    if app_id != expected_app_id or version != expected_version:
        raise SystemExit('target_schema_precondition_changed')
    if quick != 'ok' or fk_issues:
        raise SystemExit('target_integrity_precondition_failed')
    backup_con = sqlite3.connect(str(backup), timeout=5.0)
    try:
        source.backup(backup_con)
        backup_con.commit()
    finally:
        backup_con.close()
finally:
    source.close()

backup_ro, backup_app_id, backup_version, backup_counts, backup_quick, backup_fk = snapshot(backup, readonly=True)
backup_ro.close()
if (backup_app_id, backup_version, backup_counts, backup_quick, bool(backup_fk)) != (app_id, version, before_counts, 'ok', False):
    raise SystemExit('pre_migration_backup_verification_failed')

from automation.inago.maintenance.store import MaintenanceStore
store = MaintenanceStore(root)
result_version = store.initialize()
if result_version != 19:
    raise SystemExit('target_schema_version_not_19')
post, post_app_id, post_version, after_counts, post_quick, post_fk = snapshot(db, readonly=True)
post.close()
expected_counts = dict(before_counts)
expected_counts['schema_migration'] = expected_counts.get('schema_migration', 0) + 3
if post_app_id != expected_app_id or post_version != 19:
    raise SystemExit('target_schema_postcondition_failed')
if after_counts != expected_counts or post_quick != 'ok' or post_fk:
    raise SystemExit('target_historical_preservation_check_failed')
print(f'target_db={db}')
print(f'backup_db={backup}')
print(f'pre_version={version}; post_version={post_version}; application_id={post_app_id}')
print('applied_schema_migrations=17,18,19')
print(f'backup_verification=ok; pre_table_count={len(before_counts)}; pre_row_counts={before_counts}')
print(f'post_table_count={len(after_counts)}; post_row_counts={after_counts}')
print(f'post_quick_check={post_quick}; foreign_key_issues={len(post_fk)}')
'@ | & '.venv-inago/Scripts/python.exe' -B -
```

期待: same DB transitions to schema 19, backup and all existing business/history rows remain, only three `schema_migration` rows are appended, quick_check and foreign_key_check are clean. 実結果: `user_version=16→19`, `application_id=1229866829` unchanged, migration versions `17,18,19` added, 34 tables unchanged. The only row-count difference is `schema_migration=16→19`; all other counts above match exactly. `PRAGMA quick_check=ok`, foreign_key issue count 0. Command exit code **0**.

### Row-value preservation check

Count equality aloneではなく、pre-migration backupとlive DBをreadonlyで開き、全34テーブルの列と各row値を直接比較した。`schema_migration`は旧version 1〜16のrow値を比較し、新version 17〜19が追加されたことを確認した。比較対象row値やSQL内容はログに複写せず、hashも生成していない。

期待: 全既存row値とmigration前の履歴値が保持される。実結果: 34テーブル、1,333既存rowを比較し全て一致。旧migration rows 1〜16は全て一致。`sqlite_sequence`もbackup/liveで一致。checkコマンドexit **0**。

```powershell
@'
from pathlib import Path
import sqlite3

root = Path(r'C:\project\rakuten-room-inago\rakuten-room-inago\runtime').resolve(strict=True)
backup = root / 'maintenance' / 'backups' / 'state-pre-v19-20261021T075007JST.sqlite'
current = root / 'maintenance' / 'state.sqlite'

def open_ro(path):
    connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5.0)
    connection.execute('PRAGMA query_only=ON')
    connection.execute('PRAGMA busy_timeout=5000')
    return connection

old = open_ro(backup)
new = open_ro(current)
try:
    old_tables = [r[0] for r in old.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    new_tables = [r[0] for r in new.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    if old_tables != new_tables:
        raise SystemExit('table_set_changed')
    compared_rows = 0
    for table in old_tables:
        old_columns = [r[1] for r in old.execute(f'PRAGMA table_info("{table}")')]
        new_columns = [r[1] for r in new.execute(f'PRAGMA table_info("{table}")')]
        if old_columns != new_columns:
            raise SystemExit('column_set_changed')
        quoted = '"' + table.replace('"', '""') + '"'
        if table == 'schema_migration':
            old_rows = [tuple(r) for r in old.execute(f'SELECT * FROM {quoted} WHERE version <= 16')]
            new_rows = [tuple(r) for r in new.execute(f'SELECT * FROM {quoted} WHERE version <= 16')]
        else:
            old_rows = [tuple(r) for r in old.execute(f'SELECT * FROM {quoted}')]
            new_rows = [tuple(r) for r in new.execute(f'SELECT * FROM {quoted}')]
        old_rows.sort(key=repr)
        new_rows.sort(key=repr)
        if old_rows != new_rows:
            raise SystemExit('historical_row_values_changed')
        compared_rows += len(old_rows)
    try:
        old_sequence = sorted(tuple(r) for r in old.execute('SELECT name, seq FROM sqlite_sequence'))
        new_sequence = sorted(tuple(r) for r in new.execute('SELECT name, seq FROM sqlite_sequence'))
    except sqlite3.OperationalError:
        old_sequence = new_sequence = []
    if old_sequence != new_sequence:
        raise SystemExit('sqlite_sequence_changed')
    print(f'tables_compared={len(old_tables)}')
    print(f'historical_rows_compared={compared_rows}')
    print('old_migration_rows_preserved=True')
    print('sqlite_sequence_equal=True')
finally:
    old.close()
    new.close()
'@ | & '.venv-inago/Scripts/python.exe' -B -
```

## STEP04ケース結果と引継ぎ

| Case | STEP04で確認した実結果 |
|---|---|
| C07 | 実対象DB backupを作成・照合し、対象管理DBのschemaをv19へ適用。適用command exit 0。歴史row values・sequenceを保持。 |
| C08 | schema適用のみ。Worker CLI/正規受付を起動せず、queue/run/jobへ新規行を追加していない。STEP05でtarget launchに対応する既存jobの有無を読み直し、既存なら再利用、なければ正式受付する。実runner動作はSTEP05以降。 |
| C09 | 適用前後の保守Python process確認は0件、exit 0。プロセス再起動は未実施/不要。設定は変更なし。 |

本STEPではサブエージェント、AppServer turn、maintenance Worker CLI、追加修正workerを起動していない。従って追加workerの終了回収対象はなし。STEP04の所有Python migration/verification commandはいずれも終了し、exit code 0を取得。実際の10/20 launchを処理するSTEP05は親controllerに引き渡す。

未実施: service/process restart。理由: 事前・事後確認で保守Python processがなく、STEP04で起動・再起動する対象がなかった。次の開始位置はSTEP05「10月20日実launchの正規保守受付」。
