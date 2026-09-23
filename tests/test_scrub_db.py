import importlib.util
import sqlite3
from pathlib import Path

from video_security import db
from video_security.geo import ensure_table as ensure_geocode_table

_SPEC = importlib.util.spec_from_file_location(
    "scrub_db", Path(__file__).resolve().parents[1] / "scripts" / "scrub_db.py"
)
assert _SPEC is not None and _SPEC.loader is not None
scrub_db = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(scrub_db)


def _make_db(path: Path) -> None:
    conn = db.connect(str(path))
    db.init_db(conn)
    ensure_geocode_table(conn)
    job = db.create_job(conn, "/tmp/a.mp4", "hash-a")
    conn.execute(
        "INSERT INTO plates (job_id, track_id, clip_id, raw_text, norm_text) "
        "VALUES (?, 1, 0, '千葉500', '千葉500')",
        (job.id,),
    )
    conn.execute(
        "INSERT INTO plates (job_id, track_id, clip_id, raw_text, norm_text) "
        "VALUES (?, 2, 0, 'S50', 'S50')",
        (job.id,),
    )
    conn.execute(
        "INSERT INTO frame_text (job_id, clip_id, frame_number, text, text_kind, confidence) "
        "VALUES (?, 0, 1, 'store sign text', 'signage', 0.9)",
        (job.id,),
    )
    conn.execute(
        "INSERT INTO transcript_segments (job_id, clip_id, segment_id, start_time, end_time, text) "
        "VALUES (?, 0, 1, 0.0, 1.0, 'private conversation')",
        (job.id,),
    )
    conn.execute(
        "INSERT INTO gps_reverse_geocode (lat_key, lon_key, description) "
        "VALUES (35.0, 140.0, 'near home street')"
    )
    conn.execute(
        "INSERT INTO analysis_results "
        "(job_id, event_id, model_digest, prompt_version, analysis_type, raw_response) "
        "VALUES (?, 1, 'd', 'v1', 'triage', '{\"description\": \"my driveway\"}')",
        (job.id,),
    )
    conn.commit()
    conn.close()


def test_redact_plate_keeps_ken() -> None:
    assert scrub_db.redact_plate("千葉500") == "千葉■■■"
    assert scrub_db.redact_plate("S50") == "■■■"
    assert scrub_db.redact_plate("") == ""


def test_scrub(tmp_path: Path) -> None:
    src = tmp_path / "src.db"
    dst = tmp_path / "dst.db"
    _make_db(src)
    counts = scrub_db.scrub(str(src), str(dst))
    assert counts["plates"] == 2
    assert counts["frame_text"] == 1
    assert counts["transcripts"] == 1
    assert counts["geocode"] == 1
    assert counts["llm"] == 1

    conn = sqlite3.connect(dst)
    plates = conn.execute("SELECT raw_text FROM plates ORDER BY track_id").fetchall()
    assert plates == [("千葉■■■",), ("■■■",)]
    assert conn.execute("SELECT text FROM frame_text").fetchone()[0] == "[redacted]"
    hits = conn.execute(
        "SELECT COUNT(*) FROM frame_text_fts WHERE frame_text_fts MATCH 'redacted'"
    ).fetchone()[0]
    assert hits == 1
    assert (
        conn.execute("SELECT text FROM transcript_segments").fetchone()[0]
        == "[redacted]"
    )
    assert (
        conn.execute("SELECT description FROM gps_reverse_geocode").fetchone()[0]
        == "[redacted]"
    )
    assert conn.execute("SELECT raw_response FROM analysis_results").fetchone()[0] == ""
    conn.close()

    src_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    kept = src_conn.execute(
        "SELECT raw_text FROM plates WHERE track_id = 1"
    ).fetchone()[0]
    src_conn.close()
    assert kept == "千葉500"
