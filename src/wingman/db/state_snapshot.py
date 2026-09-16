"""Create and restore Git-trackable snapshots of the Wingman SQLite database."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_SNAPSHOT_PATH = Path("state/wingman-state.db.gz")
MAX_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
SNAPSHOT_FORMAT = "wingman-sqlite-gzip-v1"


class StateSnapshotError(RuntimeError):
    """Raised when a state snapshot cannot be safely saved or restored."""


@dataclass(frozen=True)
class SnapshotInfo:
    path: Path
    created_at: str
    sha256: str
    compressed_bytes: int
    uncompressed_bytes: int
    video_context_count: int | None = None
    video_context_sha256: str | None = None


@dataclass(frozen=True)
class RestoreResult:
    snapshot: SnapshotInfo
    recovery_path: Path | None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def metadata_path(snapshot_path: str | Path) -> Path:
    snapshot = Path(snapshot_path)
    return snapshot.with_name(f"{snapshot.name}.meta")


def save_state_snapshot(
    database_path: str | Path,
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
    *,
    created_at: str | None = None,
) -> SnapshotInfo:
    """Save a consistent, compact database image as deterministic gzip."""
    database = Path(database_path)
    snapshot = Path(snapshot_path)
    if not database.is_file():
        raise StateSnapshotError(f"Database not found: {database}")

    snapshot.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".wingman-state-",
        dir=snapshot.parent,
    ) as temporary_directory:
        raw_snapshot = Path(temporary_directory) / "wingman-state.db"
        _backup_database(database, raw_snapshot)
        _validate_sqlite(raw_snapshot)
        with closing(sqlite3.connect(raw_snapshot)) as compact:
            compact.execute("VACUUM")
        raw_bytes = raw_snapshot.read_bytes()
        context_count, context_digest = _video_context_fingerprint(raw_snapshot)

    compressed = gzip.compress(raw_bytes, compresslevel=9, mtime=0)
    digest = hashlib.sha256(compressed).hexdigest()
    timestamp = created_at or utc_now()
    info = SnapshotInfo(
        path=snapshot,
        created_at=timestamp,
        sha256=digest,
        compressed_bytes=len(compressed),
        uncompressed_bytes=len(raw_bytes),
        video_context_count=context_count,
        video_context_sha256=context_digest,
    )
    _atomic_write(snapshot, compressed)
    _atomic_write(metadata_path(snapshot), _format_metadata(info).encode())
    return info


def inspect_state_snapshot(
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
) -> SnapshotInfo:
    """Verify snapshot metadata, checksum, gzip data, and SQLite integrity."""
    snapshot = Path(snapshot_path)
    sidecar = metadata_path(snapshot)
    if not snapshot.is_file():
        raise StateSnapshotError(f"State snapshot not found: {snapshot}")
    if not sidecar.is_file():
        raise StateSnapshotError(f"State metadata not found: {sidecar}")

    metadata = _parse_metadata(sidecar.read_text(encoding="utf-8"))
    if metadata.get("format") != SNAPSHOT_FORMAT:
        raise StateSnapshotError("Unsupported Wingman state snapshot format.")

    compressed = snapshot.read_bytes()
    digest = hashlib.sha256(compressed).hexdigest()
    if digest != metadata.get("sha256"):
        raise StateSnapshotError("State snapshot checksum does not match metadata.")
    raw_bytes = _decompress_snapshot(compressed)

    with tempfile.TemporaryDirectory(prefix=".wingman-state-check-") as directory:
        raw_snapshot = Path(directory) / "wingman-state.db"
        raw_snapshot.write_bytes(raw_bytes)
        _validate_sqlite(raw_snapshot)
        context_count, context_digest = _video_context_fingerprint(raw_snapshot)

    expected_context_count = metadata.get("video_context_count")
    expected_context_digest = metadata.get("video_context_sha256")
    if expected_context_count is not None:
        try:
            parsed_context_count = int(expected_context_count)
        except ValueError as error:
            raise StateSnapshotError(
                "State metadata has an invalid video context count."
            ) from error
        if parsed_context_count != context_count:
            raise StateSnapshotError(
                "State snapshot video context count does not match metadata."
            )
    if (
        expected_context_digest is not None
        and expected_context_digest != context_digest
    ):
        raise StateSnapshotError(
            "State snapshot video context checksum does not match metadata."
        )

    return SnapshotInfo(
        path=snapshot,
        created_at=metadata.get("created_at", "Unknown"),
        sha256=digest,
        compressed_bytes=len(compressed),
        uncompressed_bytes=len(raw_bytes),
        video_context_count=context_count,
        video_context_sha256=context_digest,
    )


def restore_state_snapshot(
    database_path: str | Path,
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
    *,
    recovery_directory: str | Path | None = None,
) -> RestoreResult:
    """Restore a verified snapshot after backing up the current database."""
    database = Path(database_path)
    snapshot_info = inspect_state_snapshot(snapshot_path)
    compressed = snapshot_info.path.read_bytes()
    raw_bytes = _decompress_snapshot(compressed)
    database.parent.mkdir(parents=True, exist_ok=True)
    recovery_path: Path | None = None

    if database.is_file():
        recovery_root = Path(recovery_directory or ".wingman-backups")
        recovery_root.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        recovery_path = recovery_root / f"{database.stem}-{timestamp}.db"
        _backup_database(database, recovery_path)
        _validate_sqlite(recovery_path)

    with tempfile.TemporaryDirectory(prefix=".wingman-state-restore-") as directory:
        raw_snapshot = Path(directory) / "wingman-state.db"
        raw_snapshot.write_bytes(raw_bytes)
        _validate_sqlite(raw_snapshot)
        try:
            _backup_database(raw_snapshot, database)
            _validate_sqlite(database)
            restored_count, restored_digest = _video_context_fingerprint(database)
            if (
                snapshot_info.video_context_count is not None
                and restored_count != snapshot_info.video_context_count
            ):
                raise StateSnapshotError(
                    "Restored database is missing saved video context."
                )
            if (
                snapshot_info.video_context_sha256 is not None
                and restored_digest != snapshot_info.video_context_sha256
            ):
                raise StateSnapshotError(
                    "Restored video context does not match the saved state."
                )
        except Exception:
            if recovery_path is not None:
                _backup_database(recovery_path, database)
            raise

    return RestoreResult(snapshot=snapshot_info, recovery_path=recovery_path)


def _backup_database(source_path: Path, destination_path: Path) -> None:
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    source_uri = f"{source_path.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(source_uri, uri=True)) as source:
        with closing(sqlite3.connect(destination_path)) as destination:
            source.backup(destination)


def _validate_sqlite(database_path: Path) -> None:
    try:
        uri = f"{database_path.resolve().as_uri()}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            result = connection.execute("PRAGMA quick_check").fetchone()
    except sqlite3.Error as error:
        raise StateSnapshotError(
            f"Invalid SQLite database in state snapshot: {error}"
        ) from error
    if result is None or result[0] != "ok":
        detail = result[0] if result else "no integrity result"
        raise StateSnapshotError(f"SQLite integrity check failed: {detail}")


def _video_context_fingerprint(database_path: Path) -> tuple[int, str]:
    """Return a stable count and digest of manually maintained video context."""
    uri = f"{database_path.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        videos_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'videos'"
        ).fetchone()
        if videos_table is None:
            contexts: list[tuple[str, str]] = []
        else:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(videos)")
            }
            if "summary" not in columns:
                contexts = []
            else:
                contexts = [
                    (str(row[0]), str(row[1]))
                    for row in connection.execute(
                        """
                        SELECT video_id, summary
                        FROM videos
                        WHERE summary IS NOT NULL AND TRIM(summary) != ''
                        ORDER BY video_id
                        """
                    )
                ]
    serialized = json.dumps(
        contexts,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return len(contexts), hashlib.sha256(serialized).hexdigest()


def _decompress_snapshot(compressed: bytes) -> bytes:
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as archive:
            raw_bytes = archive.read(MAX_UNCOMPRESSED_BYTES + 1)
    except (gzip.BadGzipFile, EOFError, OSError) as error:
        raise StateSnapshotError("State snapshot is not valid gzip data.") from error
    if len(raw_bytes) > MAX_UNCOMPRESSED_BYTES:
        raise StateSnapshotError("State snapshot exceeds the safe size limit.")
    return raw_bytes


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _format_metadata(info: SnapshotInfo) -> str:
    return (
        f"format={SNAPSHOT_FORMAT}\n"
        f"created_at={info.created_at}\n"
        f"sha256={info.sha256}\n"
        f"compressed_bytes={info.compressed_bytes}\n"
        f"uncompressed_bytes={info.uncompressed_bytes}\n"
        f"video_context_count={info.video_context_count or 0}\n"
        f"video_context_sha256={info.video_context_sha256 or ''}\n"
    )


def _parse_metadata(content: str) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for line in content.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        metadata[key.strip()] = value.strip()
    return metadata
