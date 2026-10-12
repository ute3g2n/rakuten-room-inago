"""Apply the configured evidence-retention boundaries to SQLite state."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from automation.inago.contracts import PersistenceContractError


JST = ZoneInfo("Asia/Tokyo")
RetentionClass = Literal["raw", "detail", "aggregate"]


class RetentionError(PersistenceContractError):
    """Retention inputs or database state could not be handled safely."""


class ArtifactExpiredError(RetentionError):
    """An artifact reference exists but is no longer available as live evidence."""


@dataclass(frozen=True, slots=True)
class RetentionResult:
    now: datetime
    data_root: Path
    expired_artifact_refs: tuple[str, ...]
    expired_aggregate_refs: tuple[str, ...]
    expired_detail_observations: int
    expired_source_posts: int
    expired_keyword_snapshots: int
    deleted_revenue_orders: int
    file_deletion_failures: tuple[str, ...]

    @property
    def expired_artifacts(self) -> int:
        return len(self.expired_artifact_refs)

    @property
    def expired_aggregates(self) -> int:
        return len(self.expired_aggregate_refs)

    def open_artifact(
        self,
        connection: sqlite3.Connection,
        artifact_ref: str,
        *,
        now: str | datetime | None = None,
    ) -> Path:
        """Resolve an artifact only while its database status is live."""

        row = connection.execute(
            "SELECT path, status, retention_expires_at FROM artifact WHERE artifact_ref = ?",
            (artifact_ref,),
        ).fetchone()
        if row is None or row[1] != "available":
            raise ArtifactExpiredError("artifact is not available as live evidence")
        expires_at = row[2]
        current = _parse_jst(now, name="now") if now is not None else datetime.now(JST)
        if expires_at is not None and _parse_jst(expires_at) <= current:
            raise ArtifactExpiredError("artifact retention boundary has passed")
        path = _safe_file_path(self.data_root, row[0])
        if not path.is_file():
            raise ArtifactExpiredError("artifact content is unavailable")
        return path


def _parse_jst(value: str | datetime, *, name: str = "timestamp") -> datetime:
    try:
        parsed = (
            value
            if isinstance(value, datetime)
            else datetime.fromisoformat(value.replace("Z", "+00:00"))
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise RetentionError(f"{name} must be ISO 8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RetentionError(f"{name} must include a timezone offset")
    return parsed.astimezone(JST)


def retention_expiry(
    retention_class: RetentionClass, observed_at: str | datetime
) -> datetime:
    """Return the fixed JST expiry for raw/detail or aggregate evidence."""

    observed = _parse_jst(observed_at, name="observed_at")
    if retention_class in ("raw", "detail"):
        return observed + timedelta(days=90)
    if retention_class != "aggregate":
        raise RetentionError("retention_class must be raw, detail, or aggregate")
    try:
        return observed.replace(year=observed.year + 1)
    except ValueError:
        # A February 29 observation expires at the same JST time on February 28.
        return observed.replace(year=observed.year + 1, day=28)


def _safe_file_path(data_root: Path, value: str | Path) -> Path:
    root = data_root.resolve()
    path = Path(os.path.abspath(Path(value).expanduser()))
    try:
        relative = path.relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise RetentionError("artifact path escaped the explicit data root") from exc
    if not relative.parts:
        raise RetentionError("artifact path is not a regular child file")
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise RetentionError("artifact path contains a symbolic link")
    try:
        return path.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise RetentionError("artifact path cannot be resolved safely") from exc


def _dict_rows(
    connection: sqlite3.Connection,
    sql: str,
    parameters: tuple[object, ...] = (),
) -> list[dict[str, object]]:
    cursor = connection.execute(sql, parameters)
    names = [str(column[0]) for column in cursor.description or ()]
    return [
        {name: row[index] for index, name in enumerate(names)}
        for row in cursor.fetchall()
    ]


def _record_expired_observations(
    connection: sqlite3.Connection, now: datetime
) -> tuple[tuple[str, ...], int]:
    expired_aggregates: list[str] = []
    expired_details = 0
    rows = _dict_rows(
        connection,
        """
        SELECT observation_id, scope, observed_at, availability
        FROM metric_observation
        WHERE availability != 'expired'
        """,
    )
    for row in rows:
        retention_class: RetentionClass = (
            "aggregate" if row["scope"] == "day_aggregate" else "detail"
        )
        if retention_expiry(retention_class, row["observed_at"]) > now:
            continue
        connection.execute(
            """
            UPDATE metric_observation
            SET availability = 'expired', value_json = NULL
            WHERE observation_id = ? AND availability != 'expired'
            """,
            (row["observation_id"],),
        )
        if retention_class == "aggregate":
            expired_aggregates.append(str(row["observation_id"]))
        else:
            expired_details += 1
    return tuple(expired_aggregates), expired_details


def _record_expired_source_snapshots(
    connection: sqlite3.Connection, now: datetime
) -> tuple[int, int]:
    expired_posts = 0
    posts = _dict_rows(
        connection,
        "SELECT source_key, observed_at FROM source_post WHERE availability != 'expired'",
    )
    for row in posts:
        if retention_expiry("raw", row["observed_at"]) > now:
            continue
        connection.execute(
            """
            UPDATE source_post
            SET availability = 'expired', stable_room_id = NULL, room_url = NULL,
                item_url = NULL, image_ref = NULL, actor_ref = NULL,
                published_at = NULL, likes = NULL, comments = NULL
            WHERE source_key = ?
            """,
            (row["source_key"],),
        )
        expired_posts += 1

    expired_keywords = 0
    keywords = _dict_rows(
        connection,
        """
        SELECT snapshot_id, fetched_at FROM keyword_snapshot
        WHERE availability != 'expired' AND fetched_at IS NOT NULL
        """,
    )
    for row in keywords:
        if retention_expiry("raw", row["fetched_at"]) > now:
            continue
        connection.execute(
            """
            UPDATE keyword_snapshot
            SET availability = 'expired', words_json = '[]'
            WHERE snapshot_id = ?
            """,
            (row["snapshot_id"],),
        )
        expired_keywords += 1
    return expired_posts, expired_keywords


def apply_retention(
    connection: sqlite3.Connection,
    *,
    data_root: str | Path,
    now: str | datetime,
) -> RetentionResult:
    """Expire old evidence without removing persistent posting or run state.

    Metadata is committed before physical file removal. If a process stops after
    that commit, the database still labels the file expired and every consumer
    must reject it as live evidence; a later call can retry the physical cleanup.
    """

    root = Path(data_root).expanduser().resolve()
    current = _parse_jst(now, name="now")
    if not root.is_dir():
        raise RetentionError("data_root must already exist")

    if connection.in_transaction:
        raise RetentionError("retention must own its SQLite transaction")
    try:
        connection.execute("BEGIN IMMEDIATE")

        referenced = {
            str(row[0])
            for row in connection.execute(
                "SELECT artifact_ref FROM draft WHERE artifact_ref IS NOT NULL"
            )
        }
        artifact_rows = _dict_rows(
            connection,
            """
        SELECT artifact_ref, path, source_kind, retention_expires_at
            FROM artifact
            WHERE status = 'available' AND retention_expires_at IS NOT NULL
            """,
        )
        expired: list[tuple[str, str]] = []
        expired_refs: set[str] = set()
        for row in artifact_rows:
            if row["source_kind"] == "generation" and row["artifact_ref"] in referenced:
                continue
            if (
                _parse_jst(row["retention_expires_at"], name="retention_expires_at")
                <= current
            ):
                expired.append((str(row["artifact_ref"]), str(row["path"])))
                expired_refs.add(str(row["artifact_ref"]))

        deleted_orders = 0
        for artifact_ref, _path in expired:
            cursor = connection.execute(
                "DELETE FROM revenue_order WHERE source_ref = ?", (artifact_ref,)
            )
            deleted_orders += max(0, cursor.rowcount)
            connection.execute(
                """
                UPDATE metric_observation
            SET availability = 'expired', value_json = NULL
            WHERE source_ref = ? AND scope != 'day_aggregate'
              AND availability != 'expired'
                """,
                (artifact_ref,),
            )
            connection.execute(
                "UPDATE artifact SET status = 'expired' WHERE artifact_ref = ?",
                (artifact_ref,),
            )

        expired_aggregates, expired_details = _record_expired_observations(
            connection, current
        )
        expired_posts, expired_keywords = _record_expired_source_snapshots(
            connection, current
        )
        connection.commit()
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise

    # A prior call may have committed the expired status before a file-system
    # failure. Retry cleanup from expired rows without making the evidence live.
    expired_file_rows = _dict_rows(
        connection,
        "SELECT artifact_ref, path FROM artifact WHERE status = 'expired'",
    )
    cleanup = {str(ref): str(path) for ref, path in expired}
    cleanup.update(
        {str(row["artifact_ref"]): str(row["path"]) for row in expired_file_rows}
    )
    failures: list[str] = []
    for artifact_ref, stored_path in cleanup.items():
        try:
            path = _safe_file_path(root, stored_path)
            if path.exists():
                if not path.is_file():
                    raise RetentionError("expired artifact is not a regular file")
                path.unlink()
        except (OSError, RetentionError):
            failures.append(artifact_ref)

    return RetentionResult(
        now=current,
        data_root=root,
        expired_artifact_refs=tuple(sorted(expired_refs)),
        expired_aggregate_refs=expired_aggregates,
        expired_detail_observations=expired_details,
        expired_source_posts=expired_posts,
        expired_keyword_snapshots=expired_keywords,
        deleted_revenue_orders=deleted_orders,
        file_deletion_failures=tuple(failures),
    )
