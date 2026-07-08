import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from database import connect, initialize_database, utc_now_iso


SYNC_DIR = Path("db_sync")
EXPORTS = {
    "comments": {
        "file": SYNC_DIR / "youtube_comments.json",
        "key": "youtube_comment_id",
        "source_timestamp": "fetched_at",
        "local_timestamp": "local_updated_at",
        "local_columns": {
            "video_description",
            "wingman_reply_text",
            "youtube_reply_id",
            "replied_at",
            "skipped_until",
            "last_seen_at",
            "local_updated_at",
            "notes",
            "ai_draft_text",
            "ai_draft_model",
            "ai_draft_provider",
            "ai_drafted_at",
            "status",
        },
    },
    "instagram_comments": {
        "file": SYNC_DIR / "instagram_comments.json",
        "key": "instagram_comment_id",
        "source_timestamp": "fetched_at",
        "local_timestamp": "local_updated_at",
        "local_columns": {
            "video_description",
            "wingman_reply_text",
            "instagram_reply_id",
            "replied_at",
            "skipped_until",
            "last_seen_at",
            "local_updated_at",
            "notes",
            "ai_draft_text",
            "ai_draft_model",
            "ai_draft_provider",
            "ai_drafted_at",
            "status",
        },
    },
}


def env_value(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


def table_columns(connection, table_name: str) -> list[str]:
    return [row["name"] for row in connection.execute(f"PRAGMA table_info({table_name})")]


def comparable_timestamp(value) -> str:
    return value or ""


def load_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def write_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(rows, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def export_table(connection, table_name: str, config: dict) -> int:
    columns = [column for column in table_columns(connection, table_name) if column != "id"]
    key = config["key"]
    rows = [
        dict(row)
        for row in connection.execute(
            f"""
            SELECT {", ".join(columns)}
            FROM {table_name}
            ORDER BY {key}
            """
        )
    ]
    write_rows(config["file"], rows)
    return len(rows)


def export_database(database_path: str) -> None:
    connection = connect(database_path)
    initialize_database(connection)

    counts = {}
    for table_name, config in EXPORTS.items():
        counts[table_name] = export_table(connection, table_name, config)

    manifest = {
        "exported_at": utc_now_iso(),
        "database_path": database_path,
        "tables": counts,
    }
    write_rows(SYNC_DIR / "manifest.json", [manifest])
    connection.close()

    for table_name, count in counts.items():
        print(f"Exported {count} rows from {table_name}.")
    print(f"Wrote sync files to {SYNC_DIR}/")


def existing_row(connection, table_name: str, key: str, key_value: str) -> dict | None:
    row = connection.execute(
        f"SELECT * FROM {table_name} WHERE {key} = ?",
        (key_value,),
    ).fetchone()
    return dict(row) if row else None


def merge_row(existing: dict, incoming: dict, config: dict, columns: list[str]) -> dict:
    local_columns = set(config["local_columns"])
    source_columns = set(columns) - {"id"} - local_columns
    source_timestamp = config["source_timestamp"]
    local_timestamp = config["local_timestamp"]

    merged = dict(existing)

    incoming_source_newer = comparable_timestamp(
        incoming.get(source_timestamp)
    ) >= comparable_timestamp(existing.get(source_timestamp))
    incoming_local_newer = comparable_timestamp(
        incoming.get(local_timestamp)
    ) >= comparable_timestamp(existing.get(local_timestamp))

    for column in source_columns:
        if column not in incoming:
            continue
        if incoming_source_newer or existing.get(column) in (None, ""):
            merged[column] = incoming.get(column)

    for column in local_columns:
        if column not in incoming:
            continue
        if incoming_local_newer or existing.get(column) in (None, ""):
            merged[column] = incoming.get(column)

    return {column: merged.get(column) for column in columns if column != "id"}


def insert_row(connection, table_name: str, row: dict, columns: list[str]) -> None:
    writable_columns = [column for column in columns if column != "id" and column in row]
    placeholders = ", ".join("?" for _ in writable_columns)
    connection.execute(
        f"""
        INSERT INTO {table_name} ({", ".join(writable_columns)})
        VALUES ({placeholders})
        """,
        [row.get(column) for column in writable_columns],
    )


def update_row(
    connection, table_name: str, key: str, key_value: str, row: dict, columns: list[str]
) -> None:
    writable_columns = [
        column for column in columns if column not in {"id", key} and column in row
    ]
    assignments = ", ".join(f"{column} = ?" for column in writable_columns)
    connection.execute(
        f"""
        UPDATE {table_name}
        SET {assignments}
        WHERE {key} = ?
        """,
        [row.get(column) for column in writable_columns] + [key_value],
    )


def import_table(connection, table_name: str, config: dict) -> tuple[int, int, int]:
    rows = load_rows(config["file"])
    if not rows:
        return 0, 0, 0

    columns = table_columns(connection, table_name)
    key = config["key"]
    inserted = 0
    updated = 0
    skipped = 0

    for incoming in rows:
        key_value = incoming.get(key)
        if not key_value:
            skipped += 1
            continue

        existing = existing_row(connection, table_name, key, key_value)
        if existing:
            merged = merge_row(existing, incoming, config, columns)
            update_row(connection, table_name, key, key_value, merged, columns)
            updated += 1
        else:
            insert_row(connection, table_name, incoming, columns)
            inserted += 1

    return inserted, updated, skipped


def import_database(database_path: str) -> None:
    connection = connect(database_path)
    initialize_database(connection)

    for table_name, config in EXPORTS.items():
        inserted, updated, skipped = import_table(connection, table_name, config)
        print(
            f"Imported {table_name}: {inserted} inserted, "
            f"{updated} merged, {skipped} skipped."
        )

    connection.commit()
    connection.close()


def status() -> None:
    for table_name, config in EXPORTS.items():
        rows = load_rows(config["file"])
        print(f"{table_name}: {len(rows)} exported rows at {config['file']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export/import Wingman SQLite data through git-friendly JSON."
    )
    parser.add_argument(
        "command",
        choices=["export", "import", "status"],
        help="Export DB to JSON, import JSON into DB, or show exported row counts.",
    )
    parser.add_argument(
        "--database",
        default=env_value("DATABASE_PATH", "comments.db"),
        help="SQLite database path. Defaults to DATABASE_PATH or comments.db.",
    )
    return parser.parse_args()


def main() -> int:
    load_dotenv()
    args = parse_args()

    if args.command == "export":
        export_database(args.database)
    elif args.command == "import":
        import_database(args.database)
    else:
        status()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
