"""Tests for portable, Git-trackable Wingman database snapshots."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from wingman.cli import parse_args
from wingman.db.state_snapshot import (
    StateSnapshotError,
    inspect_state_snapshot,
    metadata_path,
    restore_state_snapshot,
    save_state_snapshot,
)


class StateSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database = self.root / "comments.db"
        self.snapshot = self.root / "state" / "wingman-state.db.gz"
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "CREATE TABLE workflow (id INTEGER PRIMARY KEY, value TEXT)"
            )
            connection.execute(
                "INSERT INTO workflow (value) VALUES ('saved value')"
            )
            connection.execute(
                """
                CREATE TABLE videos (
                    video_id TEXT PRIMARY KEY,
                    summary TEXT
                )
                """
            )
            connection.execute(
                """
                INSERT INTO videos (video_id, summary)
                VALUES (?, ?)
                """,
                (
                    "video-with-context",
                    "Manual explanation of the project.\n\n"
                    "## Response Guidance\n\n"
                    "- Mention that the debounce interval is configurable.",
                ),
            )
            connection.commit()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def value(self, database: Path) -> str:
        with closing(sqlite3.connect(database)) as connection:
            row = connection.execute(
                "SELECT value FROM workflow WHERE id = 1"
            ).fetchone()
        assert row is not None
        return row[0]

    def test_saves_valid_compressed_snapshot_and_metadata(self) -> None:
        saved = save_state_snapshot(
            self.database,
            self.snapshot,
            created_at="2026-07-27T14:00:00Z",
        )

        inspected = inspect_state_snapshot(self.snapshot)
        self.assertTrue(self.snapshot.is_file())
        self.assertTrue(metadata_path(self.snapshot).is_file())
        self.assertEqual(inspected.created_at, "2026-07-27T14:00:00Z")
        self.assertEqual(inspected.sha256, saved.sha256)
        self.assertLess(inspected.compressed_bytes, inspected.uncompressed_bytes)
        self.assertEqual(saved.video_context_count, 1)
        self.assertEqual(inspected.video_context_count, 1)
        self.assertEqual(
            inspected.video_context_sha256,
            saved.video_context_sha256,
        )

    def test_restore_preserves_recovery_copy_of_current_database(self) -> None:
        save_state_snapshot(self.database, self.snapshot)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "UPDATE workflow SET value = 'newer local value' WHERE id = 1"
            )
            connection.commit()
        recovery_directory = self.root / "backups"

        result = restore_state_snapshot(
            self.database,
            self.snapshot,
            recovery_directory=recovery_directory,
        )

        self.assertEqual(self.value(self.database), "saved value")
        assert result.recovery_path is not None
        self.assertTrue(result.recovery_path.is_file())
        self.assertEqual(
            self.value(result.recovery_path),
            "newer local value",
        )
        with closing(sqlite3.connect(self.database)) as connection:
            restored_context = connection.execute(
                "SELECT summary FROM videos WHERE video_id = ?",
                ("video-with-context",),
            ).fetchone()
        assert restored_context is not None
        self.assertIn("Manual explanation", restored_context[0])
        self.assertIn("## Response Guidance", restored_context[0])

    def test_context_metadata_tampering_is_rejected(self) -> None:
        save_state_snapshot(self.database, self.snapshot)
        sidecar = metadata_path(self.snapshot)
        metadata = sidecar.read_text(encoding="utf-8")
        sidecar.write_text(
            metadata.replace("video_context_count=1", "video_context_count=2"),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            StateSnapshotError,
            "video context count does not match",
        ):
            inspect_state_snapshot(self.snapshot)

    def test_checksum_failure_does_not_modify_current_database(self) -> None:
        save_state_snapshot(self.database, self.snapshot)
        corrupted = bytearray(self.snapshot.read_bytes())
        corrupted[-1] ^= 1
        self.snapshot.write_bytes(corrupted)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "UPDATE workflow SET value = 'keep local value' WHERE id = 1"
            )
            connection.commit()

        with self.assertRaisesRegex(
            StateSnapshotError,
            "checksum does not match",
        ):
            restore_state_snapshot(self.database, self.snapshot)

        self.assertEqual(self.value(self.database), "keep local value")

    def test_cli_exposes_save_restore_and_status_actions(self) -> None:
        self.assertEqual(
            parse_args(["state", "save"]).state_action,
            "save",
        )
        self.assertEqual(
            parse_args(["state", "restore"]).state_action,
            "restore",
        )
        self.assertEqual(
            parse_args(["state", "status"]).state_action,
            "status",
        )


if __name__ == "__main__":
    unittest.main()
