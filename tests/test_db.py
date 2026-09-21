from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from video_security.db import (
    MIGRATIONS,
    connect,
    create_job,
    get_job_by_hash,
    init_db,
    list_jobs,
    update_job_status,
)


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "test.db")


EXPECTED_TABLES = {
    "jobs",
    "frames",
    "events",
    "vehicle_tracks",
    "plates",
    "frame_text",
    "frame_text_fts",
    "transcript_segments",
    "analysis_results",
    "cameras",
    "clips",
    "clip_gps_data",
    "sessions",
    "photos_imports",
    "archived_originals",
}


def get_table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view') ORDER BY name"
    ).fetchall()
    return {r[0] for r in rows}


def test_migrations_creates_all_tables(db_path: str) -> None:
    conn = connect(db_path)
    init_db(conn)
    tables = get_table_names(conn)
    missing = EXPECTED_TABLES - tables
    assert not missing, f"Missing tables: {missing}"
    conn.close()


def test_migrations_idempotent(db_path: str) -> None:
    conn = connect(db_path)
    init_db(conn)
    version_after_first: int = conn.execute("PRAGMA user_version").fetchone()[0]
    init_db(conn)
    version_after_second: int = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version_after_first == version_after_second
    conn.close()


def test_migrations_reconnect(db_path: str) -> None:
    conn1 = connect(db_path)
    init_db(conn1)
    conn1.close()
    conn2 = connect(db_path)
    init_db(conn2)
    tables = get_table_names(conn2)
    missing = EXPECTED_TABLES - tables
    assert not missing, f"Missing tables after reconnect: {missing}"
    conn2.close()


def test_job_insert_and_query(db_path: str) -> None:
    conn = connect(db_path)
    init_db(conn)
    job = create_job(conn, "/videos/test.mp4", "abc123")
    assert job.id is not None
    assert job.video_path == "/videos/test.mp4"
    assert job.video_hash == "abc123"
    assert job.status == "pending"

    found = get_job_by_hash(conn, "abc123")
    assert found is not None
    assert found.id == job.id
    assert found.video_path == "/videos/test.mp4"

    not_found = get_job_by_hash(conn, "nonexistent")
    assert not_found is None
    conn.close()


def test_update_job_status(db_path: str) -> None:
    conn = connect(db_path)
    init_db(conn)
    job = create_job(conn, "/videos/test.mp4", "abc123")
    update_job_status(conn, job.id, "done")
    updated = get_job_by_hash(conn, "abc123")
    assert updated is not None
    assert updated.status == "done"
    conn.close()


def test_list_jobs(db_path: str) -> None:
    conn = connect(db_path)
    init_db(conn)
    job1 = create_job(conn, "/videos/one.mp4", "hash1")
    job2 = create_job(conn, "/videos/two.mp4", "hash2")
    jobs = list_jobs(conn)
    assert len(jobs) == 2
    assert {j.id for j in jobs} == {job1.id, job2.id}
    conn.close()


def test_fts5_trigger_sync(db_path: str) -> None:
    conn = connect(db_path)
    init_db(conn)
    conn.execute(
        "INSERT INTO frame_text (job_id, clip_id, frame_number, text, text_kind, confidence) "
        "VALUES (1, 1, 100, 'hello world', 'signage', 0.9)"
    )
    conn.commit()
    results = conn.execute(
        "SELECT * FROM frame_text_fts WHERE frame_text_fts MATCH 'hello'"
    ).fetchall()
    assert len(results) > 0

    conn.execute("UPDATE frame_text SET text = 'goodbye world' WHERE id = 1")
    conn.commit()
    old = conn.execute(
        "SELECT * FROM frame_text_fts WHERE frame_text_fts MATCH 'hello'"
    ).fetchall()
    assert len(old) == 0
    new = conn.execute(
        "SELECT * FROM frame_text_fts WHERE frame_text_fts MATCH 'goodbye'"
    ).fetchall()
    assert len(new) > 0

    conn.execute("DELETE FROM frame_text WHERE id = 1")
    conn.commit()
    deleted = conn.execute(
        "SELECT * FROM frame_text_fts WHERE frame_text_fts MATCH 'goodbye'"
    ).fetchall()
    assert len(deleted) == 0
    conn.close()


def test_user_version_tracks_migration_count(db_path: str) -> None:
    conn = connect(db_path)
    init_db(conn)
    version: int = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == len(MIGRATIONS)
    conn.close()


def test_photos_imports_migration_idempotent(db_path: str) -> None:
    conn = connect(db_path)
    init_db(conn)
    init_db(conn)
    tables = get_table_names(conn)
    assert "photos_imports" in tables
    conn.execute(
        "INSERT INTO photos_imports (uuid, job_id, video_hash, filename) VALUES (?,?,?,?)",
        ("test-uuid", 1, "abc123", "test.mov"),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM photos_imports WHERE uuid = ?", ("test-uuid",)).fetchone()
    assert row is not None
    conn.close()


def test_photos_import_get_unknown(db_path: str) -> None:
    from video_security.db import get_photos_import

    conn = connect(db_path)
    init_db(conn)
    assert get_photos_import(conn, "nonexistent") is None
    conn.close()


def test_photos_import_insert_and_get(db_path: str) -> None:
    from video_security.db import get_photos_import, insert_photos_import

    conn = connect(db_path)
    init_db(conn)
    insert_photos_import(conn, "uuid-1", 42, "hash42", "clip.mov")
    row = get_photos_import(conn, "uuid-1")
    assert row is not None
    assert row["uuid"] == "uuid-1"
    assert row["job_id"] == 42
    assert row["video_hash"] == "hash42"
    assert row["filename"] == "clip.mov"
    assert row["imported_at"] is not None
    conn.close()


def test_photos_import_insert_or_replace_overwrites(db_path: str) -> None:
    from video_security.db import get_photos_import, insert_photos_import

    conn = connect(db_path)
    init_db(conn)
    insert_photos_import(conn, "uuid-1", 42, "hash42", "clip.mov")
    insert_photos_import(conn, "uuid-1", 99, "hash99", "clip.mov")
    row = get_photos_import(conn, "uuid-1")
    assert row is not None
    assert row["job_id"] == 99
    assert row["video_hash"] == "hash99"
    conn.close()


def test_archived_originals_migration_idempotent(db_path: str) -> None:
    conn = connect(db_path)
    init_db(conn)
    init_db(conn)
    tables = get_table_names(conn)
    assert "archived_originals" in tables
    conn.execute(
        "INSERT INTO archived_originals (job_id, original_path, original_bytes) VALUES (?,?,?)",
        (1, "/cold/storage/original.mp4", 52428800),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM archived_originals WHERE job_id = 1").fetchone()
    assert row is not None
    conn.close()


def test_archived_originals_get_unknown(db_path: str) -> None:
    from video_security.db import get_archived_original

    conn = connect(db_path)
    init_db(conn)
    assert get_archived_original(conn, 999) is None
    conn.close()


def test_archived_originals_insert_and_get(db_path: str) -> None:
    from video_security.db import get_archived_original, insert_archived_original

    conn = connect(db_path)
    init_db(conn)
    insert_archived_original(conn, 42, "/cold/storage/original.mp4", 52428800, 10485760)
    row = get_archived_original(conn, 42)
    assert row is not None
    assert row["job_id"] == 42
    assert row["original_path"] == "/cold/storage/original.mp4"
    assert row["original_bytes"] == 52428800
    assert row["proxy_bytes"] == 10485760
    assert row["archived_at"] is not None
    conn.close()


def test_archived_originals_delete_removes(db_path: str) -> None:
    from video_security.db import (
        delete_archived_original,
        get_archived_original,
        insert_archived_original,
    )

    conn = connect(db_path)
    init_db(conn)
    insert_archived_original(conn, 42, "/cold/storage/original.mp4", 52428800, None)
    assert get_archived_original(conn, 42) is not None
    delete_archived_original(conn, 42)
    assert get_archived_original(conn, 42) is None
    conn.close()