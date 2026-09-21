import dataclasses
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from video_security.config import Config
from video_security.fs import spotlight_ignore


@dataclasses.dataclass
class JobRow:
    id: int
    video_path: str
    video_hash: str
    status: str
    total_frames: int | None
    current_frame: int
    current_stage: str
    recording_start_utc: str | None
    import_id: str | None
    created_at: str
    updated_at: str


def _row_to_job(row: sqlite3.Row) -> JobRow:
    return JobRow(
        id=row["id"],
        video_path=row["video_path"],
        video_hash=row["video_hash"],
        status=row["status"],
        total_frames=row["total_frames"],
        current_frame=row["current_frame"],
        current_stage=row["current_stage"],
        recording_start_utc=row["recording_start_utc"],
        import_id=row["import_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


MIGRATIONS: list[list[str]] = [
    [
        """CREATE TABLE jobs (
            id INTEGER PRIMARY KEY,
            video_path TEXT NOT NULL,
            video_hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            total_frames INTEGER,
            current_frame INTEGER DEFAULT 0,
            current_stage TEXT DEFAULT 'pending',
            recording_start_utc TEXT,
            import_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE frames (
            job_id INTEGER NOT NULL,
            clip_id INTEGER NOT NULL,
            frame_number INTEGER NOT NULL,
            timestamp_sec REAL NOT NULL,
            dhash INTEGER,
            lighting_condition TEXT,
            PRIMARY KEY (job_id, clip_id, frame_number)
        )""",
        """CREATE TABLE events (
            id INTEGER PRIMARY KEY,
            job_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            start_sec REAL NOT NULL,
            end_sec REAL NOT NULL,
            clip_id INTEGER NOT NULL,
            track_id INTEGER,
            keyframes_json TEXT DEFAULT '[]',
            faces_json TEXT,
            detector_score REAL NOT NULL,
            priority REAL NOT NULL DEFAULT 0.5,
            status TEXT NOT NULL DEFAULT 'pending',
            llm_result_id INTEGER
        )""",
        """CREATE TABLE vehicle_tracks (
            job_id INTEGER NOT NULL,
            track_id INTEGER NOT NULL,
            clip_id INTEGER NOT NULL,
            first_frame INTEGER NOT NULL,
            last_frame INTEGER NOT NULL,
            weaving_score REAL,
            direction TEXT,
            PRIMARY KEY (job_id, track_id, clip_id)
        )""",
        """CREATE TABLE plates (
            job_id INTEGER NOT NULL,
            track_id INTEGER NOT NULL,
            clip_id INTEGER NOT NULL,
            raw_text TEXT,
            norm_text TEXT,
            confidence REAL,
            best_frame INTEGER,
            ocr_votes_json TEXT,
            PRIMARY KEY (job_id, track_id, clip_id)
        )""",
        """CREATE TABLE frame_text (
            id INTEGER PRIMARY KEY,
            job_id INTEGER NOT NULL,
            clip_id INTEGER NOT NULL,
            frame_number INTEGER NOT NULL,
            text TEXT NOT NULL,
            text_kind TEXT NOT NULL,
            region_json TEXT,
            confidence REAL NOT NULL
        )""",
        """CREATE VIRTUAL TABLE frame_text_fts USING fts5(
            text,
            content=frame_text,
            content_rowid=id
        )""",
        """CREATE TABLE transcript_segments (
            job_id INTEGER NOT NULL,
            clip_id INTEGER NOT NULL,
            segment_id INTEGER NOT NULL,
            start_time REAL NOT NULL,
            end_time REAL NOT NULL,
            text TEXT NOT NULL,
            language TEXT,
            PRIMARY KEY (job_id, clip_id, segment_id)
        )""",
        """CREATE TABLE analysis_results (
            id INTEGER PRIMARY KEY,
            job_id INTEGER NOT NULL,
            event_id INTEGER NOT NULL,
            model_digest TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            analysis_type TEXT NOT NULL,
            raw_response TEXT NOT NULL,
            confidence REAL,
            retry_count INTEGER DEFAULT 0,
            tiled_results TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE cameras (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            source_pattern TEXT NOT NULL,
            config_json TEXT NOT NULL
        )""",
        """CREATE TABLE clips (
            job_id INTEGER NOT NULL,
            clip_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            channel TEXT NOT NULL DEFAULT 'front',
            priority REAL NOT NULL DEFAULT 0.5,
            recording_start_utc TEXT,
            duration_sec REAL,
            lighting TEXT DEFAULT 'day',
            has_audio BOOLEAN DEFAULT TRUE,
            PRIMARY KEY (job_id, clip_id)
        )""",
        """CREATE TABLE clip_gps_data (
            job_id INTEGER NOT NULL,
            clip_id INTEGER NOT NULL,
            time_sec REAL NOT NULL,
            lat REAL,
            lon REAL,
            speed_kmh REAL,
            bearing REAL,
            ax REAL,
            ay REAL,
            az REAL,
            PRIMARY KEY (job_id, clip_id, time_sec)
        )""",
        """CREATE TABLE sessions (
            job_id INTEGER NOT NULL,
            session_id TEXT NOT NULL,
            clips_json TEXT NOT NULL
        )""",
        """CREATE INDEX idx_frames_job_frame ON frames(job_id, frame_number)""",
        """CREATE INDEX idx_frame_text_job_frame ON frame_text(job_id, frame_number)""",
        "CREATE TRIGGER frame_text_ai AFTER INSERT ON frame_text BEGIN "
        "INSERT INTO frame_text_fts(rowid, text) VALUES (new.id, new.text); END",
        "CREATE TRIGGER frame_text_ad AFTER DELETE ON frame_text BEGIN "
        "INSERT INTO frame_text_fts(frame_text_fts, rowid, text) "
        "VALUES('delete', old.id, old.text); END",
        "CREATE TRIGGER frame_text_au AFTER UPDATE ON frame_text BEGIN "
        "INSERT INTO frame_text_fts(frame_text_fts, rowid, text) "
        "VALUES('delete', old.id, old.text); "
        "INSERT INTO frame_text_fts(rowid, text) VALUES (new.id, new.text); END",
    ],
    [
        "ALTER TABLE jobs ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0",
    ],
    [
        "CREATE INDEX IF NOT EXISTS idx_events_job_start ON events(job_id, start_sec)",
        "CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type)",
        "CREATE INDEX IF NOT EXISTS idx_plates_norm ON plates(norm_text)",
        "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)",
        "CREATE INDEX IF NOT EXISTS idx_jobs_import ON jobs(import_id)",
    ],
    [
        """CREATE TABLE faces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            event_id INTEGER NOT NULL,
            keyframe_index INTEGER NOT NULL,
            face_index INTEGER NOT NULL,
            crop_path TEXT NOT NULL,
            quality REAL,
            embedding BLOB,
            person_id INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE persons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sightings INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        "CREATE INDEX IF NOT EXISTS idx_faces_job ON faces(job_id)",
        "CREATE INDEX IF NOT EXISTS idx_faces_event ON faces(event_id)",
        "CREATE INDEX IF NOT EXISTS idx_faces_person ON faces(person_id)",
    ],
    [
        """CREATE TABLE watchlists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            pattern TEXT NOT NULL,
            note TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE watchlist_hits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            watchlist_id INTEGER NOT NULL,
            job_id INTEGER NOT NULL,
            event_id INTEGER,
            detail TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        "CREATE INDEX IF NOT EXISTS idx_watchlist_hits_job ON watchlist_hits(job_id)",
        "CREATE INDEX IF NOT EXISTS idx_watchlist_hits_wl ON watchlist_hits(watchlist_id)",
    ],
    [
        """CREATE TABLE IF NOT EXISTS event_embeddings (
            event_id INTEGER PRIMARY KEY,
            embedding BLOB NOT NULL,
            model TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
    ],
    [
        """CREATE TABLE IF NOT EXISTS transcript_embeddings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            clip_id INTEGER NOT NULL DEFAULT 0,
            segment_id INTEGER NOT NULL,
            start_time REAL,
            end_time REAL,
            text TEXT NOT NULL,
            embedding BLOB NOT NULL,
            model TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (job_id, clip_id, segment_id, model)
        )""",
    ],
    [
        """CREATE TABLE photos_imports (
            uuid TEXT PRIMARY KEY,
            job_id INTEGER NOT NULL,
            video_hash TEXT NOT NULL,
            filename TEXT NOT NULL,
            imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE INDEX idx_photos_imports_hash ON photos_imports(video_hash)""",
    ],
    [
        """CREATE TABLE archived_originals (
            job_id INTEGER PRIMARY KEY,
            original_path TEXT NOT NULL,
            original_bytes INTEGER NOT NULL,
            proxy_bytes INTEGER,
            archived_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
    ],
    [
        "ALTER TABLE archived_originals ADD COLUMN location TEXT NOT NULL DEFAULT 'cold'",
    ],
    [
        "ALTER TABLE archived_originals ADD COLUMN proxy_cold_path TEXT",
    ],
]


def connect(db_path: str) -> sqlite3.Connection:
    parent = Path(db_path).expanduser().parent
    if str(parent) not in ("", "."):
        parent.mkdir(parents=True, exist_ok=True)
        spotlight_ignore(parent)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


_FTS_BAREWORD_RE = re.compile(r"^[\w\-]+$", re.UNICODE)


def fts_match(
    conn: sqlite3.Connection,
    sql: str,
    query: str,
    params: tuple[Any, ...] = (),
) -> list[sqlite3.Row]:
    try:
        return conn.execute(sql, (query, *params)).fetchall()
    except sqlite3.OperationalError:
        if not _FTS_BAREWORD_RE.match(query):
            raise
        return conn.execute(sql, (f'"{query}"', *params)).fetchall()


def init_db(conn: sqlite3.Connection) -> None:
    current_version: int = conn.execute("PRAGMA user_version").fetchone()[0]
    for idx in range(current_version, len(MIGRATIONS)):
        conn.execute("BEGIN IMMEDIATE")
        for stmt in MIGRATIONS[idx]:
            conn.execute(stmt)
        conn.execute(f"PRAGMA user_version = {idx + 1}")
        conn.commit()
    cols = [row[1] for row in conn.execute("PRAGMA table_info(events)").fetchall()]
    if "faces_json" not in cols:
        conn.execute("ALTER TABLE events ADD COLUMN faces_json TEXT")
        conn.commit()
    plate_cols = [row[1] for row in conn.execute("PRAGMA table_info(plates)").fetchall()]
    if "crop_path" not in plate_cols:
        conn.execute("ALTER TABLE plates ADD COLUMN crop_path TEXT")
        conn.commit()
    vt_cols = [row[1] for row in conn.execute("PRAGMA table_info(vehicle_tracks)").fetchall()]
    if "strip_json" not in vt_cols:
        conn.execute("ALTER TABLE vehicle_tracks ADD COLUMN strip_json TEXT")
        conn.commit()
    if "class_id" not in vt_cols:
        conn.execute(
            "ALTER TABLE vehicle_tracks ADD COLUMN class_id INTEGER NOT NULL DEFAULT 2"
        )
        conn.commit()
    job_cols = [row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()]
    if "evidence_json" not in job_cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN evidence_json TEXT")
        conn.commit()
    if "metadata_json" not in job_cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN metadata_json TEXT")
        conn.commit()


def create_job(conn: sqlite3.Connection, video_path: str, video_hash: str) -> JobRow:
    cur = conn.execute(
        "INSERT INTO jobs (video_path, video_hash) VALUES (?, ?) RETURNING *",
        (video_path, video_hash),
    )
    row = cur.fetchone()
    cur.close()
    conn.commit()
    return _row_to_job(row)


def get_job_by_hash(conn: sqlite3.Connection, video_hash: str) -> JobRow | None:
    row = conn.execute(
        "SELECT * FROM jobs WHERE video_hash = ?", (video_hash,)
    ).fetchone()
    if row is None:
        return None
    return _row_to_job(row)


def get_job_by_id(conn: sqlite3.Connection, job_id: int) -> JobRow | None:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        return None
    return _row_to_job(row)


def get_job_by_video_path(conn: sqlite3.Connection, video_path: str) -> JobRow | None:
    row = conn.execute(
        "SELECT * FROM jobs WHERE video_path = ?", (video_path,)
    ).fetchone()
    if row is None:
        return None
    return _row_to_job(row)


def update_job_status(conn: sqlite3.Connection, job_id: int, status: str) -> None:
    conn.execute(
        "UPDATE jobs SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (status, job_id),
    )
    conn.commit()


def update_job_evidence(
    conn: sqlite3.Connection, job_id: int, evidence_json: str
) -> None:
    conn.execute(
        "UPDATE jobs SET evidence_json = ?, updated_at = CURRENT_TIMESTAMP "
        "WHERE id = ?",
        (evidence_json, job_id),
    )
    conn.commit()


def get_job_evidence(
    conn: sqlite3.Connection, job_id: int
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT evidence_json FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    if row is None or row["evidence_json"] is None:
        return None
    try:
        data = json.loads(row["evidence_json"])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def set_job_import_meta(
    conn: sqlite3.Connection,
    job_id: int,
    import_id: str,
    recording_start_utc: str | None,
) -> None:
    conn.execute(
        "UPDATE jobs SET import_id = ?, recording_start_utc = ?, "
        "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (import_id, recording_start_utc, job_id),
    )
    conn.commit()


def insert_clip(
    conn: sqlite3.Connection,
    job_id: int,
    clip_id: int,
    filename: str,
    channel: str,
    priority: float,
    recording_start_utc: str | None,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO clips "
        "(job_id, clip_id, filename, channel, priority, recording_start_utc) "
        "VALUES (?,?,?,?,?,?)",
        (job_id, clip_id, filename, channel, priority, recording_start_utc),
    )
    conn.commit()


def insert_session(
    conn: sqlite3.Connection,
    job_id: int,
    session_id: str,
    clips_json: str,
) -> None:
    conn.execute(
        "DELETE FROM sessions WHERE job_id = ? AND session_id = ?",
        (job_id, session_id),
    )
    conn.execute(
        "INSERT INTO sessions (job_id, session_id, clips_json) VALUES (?,?,?)",
        (job_id, session_id, clips_json),
    )
    conn.commit()


def insert_gps_row(
    conn: sqlite3.Connection,
    job_id: int,
    clip_id: int,
    time_sec: float,
    lat: float | None,
    lon: float | None,
    speed_kmh: float | None,
    bearing: float | None,
    ax: float | None,
    ay: float | None,
    az: float | None,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO clip_gps_data "
        "(job_id, clip_id, time_sec, lat, lon, speed_kmh, bearing, ax, ay, az) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (job_id, clip_id, time_sec, lat, lon, speed_kmh, bearing, ax, ay, az),
    )


def list_jobs(conn: sqlite3.Connection) -> list[JobRow]:
    rows = conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()
    return [_row_to_job(r) for r in rows]


def insert_frame(
    conn: sqlite3.Connection,
    job_id: int,
    clip_id: int,
    frame_number: int,
    timestamp_sec: float,
    dhash: int | None,
    lighting_condition: str | None,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO frames "
        "(job_id, clip_id, frame_number, timestamp_sec, dhash, lighting_condition) "
        "VALUES (?,?,?,?,?,?)",
        (job_id, clip_id, frame_number, timestamp_sec, dhash, lighting_condition),
    )
    conn.commit()


def insert_transcript_segment(
    conn: sqlite3.Connection,
    job_id: int,
    clip_id: int,
    segment_id: int,
    start_time: float,
    end_time: float,
    text: str,
    language: str | None,
) -> None:
    conn.execute(
        "INSERT INTO transcript_segments "
        "(job_id, clip_id, segment_id, start_time, end_time, text, language) "
        "VALUES (?,?,?,?,?,?,?)",
        (job_id, clip_id, segment_id, start_time, end_time, text, language),
    )
    conn.commit()


def insert_event(
    conn: sqlite3.Connection,
    job_id: int,
    event_type: str,
    start_sec: float,
    end_sec: float,
    clip_id: int,
    track_id: int | None,
    keyframes_json: str,
    detector_score: float,
    priority: float,
    faces_json: str | None = None,
) -> int:
    if faces_json is not None:
        cur = conn.execute(
            "INSERT INTO events "
            "(job_id, event_type, start_sec, end_sec, clip_id, track_id, keyframes_json, "
            "detector_score, priority, faces_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?) RETURNING id",
            (
                job_id, event_type, start_sec, end_sec, clip_id, track_id,
                keyframes_json, detector_score, priority, faces_json,
            ),
        )
    else:
        cur = conn.execute(
            "INSERT INTO events "
            "(job_id, event_type, start_sec, end_sec, clip_id, track_id, keyframes_json, "
            "detector_score, priority) "
            "VALUES (?,?,?,?,?,?,?,?,?) RETURNING id",
            (
                job_id, event_type, start_sec, end_sec, clip_id, track_id,
                keyframes_json, detector_score, priority,
            ),
        )
    row = cur.fetchone()
    cur.close()
    conn.commit()
    return int(row["id"])


def insert_plate(
    conn: sqlite3.Connection,
    job_id: int,
    track_id: int,
    clip_id: int,
    raw_text: str | None,
    norm_text: str | None,
    confidence: float | None,
    best_frame: int | None,
    ocr_votes_json: str | None,
    crop_path: str | None = None,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO plates "
        "(job_id, track_id, clip_id, raw_text, norm_text, confidence, best_frame, ocr_votes_json, "
        "crop_path) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (job_id, track_id, clip_id, raw_text, norm_text, confidence, best_frame, ocr_votes_json,
         crop_path),
    )
    conn.commit()


def insert_frame_text(
    conn: sqlite3.Connection,
    job_id: int,
    clip_id: int,
    frame_number: int,
    text: str,
    text_kind: str,
    region_json: str | None,
    confidence: float,
) -> None:
    conn.execute(
        "INSERT INTO frame_text "
        "(job_id, clip_id, frame_number, text, text_kind, region_json, confidence) "
        "VALUES (?,?,?,?,?,?,?)",
        (job_id, clip_id, frame_number, text, text_kind, region_json, confidence),
    )
    conn.commit()


def insert_vehicle_track(
    conn: sqlite3.Connection,
    job_id: int,
    track_id: int,
    clip_id: int,
    first_frame: int,
    last_frame: int,
    weaving_score: float | None,
    direction: str | None,
    class_id: int = 2,
) -> None:
    conn.execute(
        "INSERT INTO vehicle_tracks "
        "(job_id, track_id, clip_id, first_frame, last_frame, weaving_score, "
        "direction, class_id) VALUES (?,?,?,?,?,?,?,?)",
        (
            job_id,
            track_id,
            clip_id,
            first_frame,
            last_frame,
            weaving_score,
            direction,
            class_id,
        ),
    )
    conn.commit()


def update_vehicle_track_strip(
    conn: sqlite3.Connection,
    job_id: int,
    track_id: int,
    strip_json: str | None,
) -> None:
    conn.execute(
        "UPDATE vehicle_tracks SET strip_json = ? WHERE job_id = ? AND track_id = ?",
        (strip_json, job_id, track_id),
    )
    conn.commit()


def insert_analysis_result(
    conn: sqlite3.Connection,
    job_id: int,
    event_id: int,
    model_digest: str,
    prompt_version: str,
    analysis_type: str,
    raw_response: str,
    confidence: float | None,
    retry_count: int,
    tiled_results: str | None,
) -> int:
    cur = conn.execute(
        "INSERT INTO analysis_results "
        "(job_id, event_id, model_digest, prompt_version, analysis_type, raw_response, "
        "confidence, retry_count, tiled_results) "
        "VALUES (?,?,?,?,?,?,?,?,?) RETURNING id",
        (
            job_id, event_id, model_digest, prompt_version, analysis_type,
            raw_response, confidence, retry_count, tiled_results
        ),
    )
    row = cur.fetchone()
    cur.close()
    conn.commit()
    return int(row["id"])


def update_event_status(
    conn: sqlite3.Connection,
    event_id: int,
    status: str,
    llm_result_id: int | None = None,
) -> None:
    conn.execute(
        "UPDATE events SET status = ?, llm_result_id = ? WHERE id = ?",
        (status, llm_result_id, event_id),
    )
    conn.commit()


def update_event_keyframes(
    conn: sqlite3.Connection,
    event_id: int,
    keyframes_json: str,
) -> None:
    conn.execute(
        "UPDATE events SET keyframes_json = ? WHERE id = ?",
        (keyframes_json, event_id),
    )
    conn.commit()


def update_event_faces(
    conn: sqlite3.Connection,
    event_id: int,
    faces_json: str,
) -> None:
    conn.execute(
        "UPDATE events SET faces_json = ? WHERE id = ?",
        (faces_json, event_id),
    )
    conn.commit()


def get_events_for_job(
    conn: sqlite3.Connection,
    job_id: int,
    status: str | None = None,
) -> list[sqlite3.Row]:
    if status is None:
        return conn.execute(
            "SELECT * FROM events WHERE job_id = ? ORDER BY id", (job_id,)
        ).fetchall()
    return conn.execute(
        "SELECT * FROM events WHERE job_id = ? AND status = ? ORDER BY id",
        (job_id, status),
    ).fetchall()


def delete_job_rows(
    conn: sqlite3.Connection, job_id: int, artifact_dir: str | None = None
) -> None:
    tables = [
        "frames", "events", "vehicle_tracks", "plates", "frame_text",
        "transcript_segments", "analysis_results", "sessions", "clips",
        "clip_gps_data", "faces", "watchlist_hits",
    ]
    for table in tables:
        conn.execute(f"DELETE FROM {table} WHERE job_id = ?", (job_id,))
    conn.commit()
    from video_security.identity import reconcile_persons

    reconcile_persons(conn)
    if artifact_dir:
        import shutil
        for sub in ("frames", "plates", "faces"):
            shutil.rmtree(Path(artifact_dir) / sub / str(job_id), ignore_errors=True)


def face_crop_path(
    cfg: Config, job_id: int, event_id: int, kf_index: int, face_index: int
) -> Path | None:
    base = Path(cfg.storage.artifact_dir).expanduser()
    cand = base / "faces" / str(job_id) / f"face_{event_id}_{kf_index}_{face_index}.jpg"
    return cand if cand.is_file() else None


def events_for_plate(
    conn: sqlite3.Connection, job_id: int, track_id: int
) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM events WHERE job_id = ? AND track_id = ? ORDER BY start_sec",
        (job_id, track_id),
    ).fetchall()


def nearest_event_to_seconds(
    conn: sqlite3.Connection, job_id: int, track_id: int, target_seconds: float
) -> sqlite3.Row | None:
    rows = events_for_plate(conn, job_id, track_id)
    if not rows:
        return None
    return min(rows, key=lambda r: abs((r["start_sec"] + r["end_sec"]) / 2 - target_seconds))


def get_photos_import(conn: sqlite3.Connection, uuid: str) -> sqlite3.Row | None:
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM photos_imports WHERE uuid = ?", (uuid,)
    ).fetchone()
    return row


def insert_photos_import(
    conn: sqlite3.Connection, uuid: str, job_id: int, video_hash: str, filename: str
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO photos_imports (uuid, job_id, video_hash, filename) "
        "VALUES (?,?,?,?)",
        (uuid, job_id, video_hash, filename),
    )
    conn.commit()


def get_archived_original(conn: sqlite3.Connection, job_id: int) -> sqlite3.Row | None:
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM archived_originals WHERE job_id = ?", (job_id,)
    ).fetchone()
    return row


def insert_archived_original(
    conn: sqlite3.Connection,
    job_id: int,
    original_path: str,
    original_bytes: int,
    proxy_bytes: int | None,
    location: str = "cold",
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO archived_originals "
        "(job_id, original_path, original_bytes, proxy_bytes, location) "
        "VALUES (?,?,?,?,?)",
        (job_id, original_path, original_bytes, proxy_bytes, location),
    )
    conn.commit()


def delete_archived_original(conn: sqlite3.Connection, job_id: int) -> None:
    conn.execute(
        "DELETE FROM archived_originals WHERE job_id = ?", (job_id,)
    )
    conn.commit()


def clip_fps(conn: sqlite3.Connection, job_id: int) -> float:
    row = conn.execute(
        "SELECT MIN(timestamp_sec) lo, MAX(timestamp_sec) hi, "
        "MIN(frame_number) f0, MAX(frame_number) f1 FROM frames WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    if row is None or row["hi"] is None or row["f1"] is None:
        return 30.0
    if row["f1"] == row["f0"]:
        return 30.0
    span = row["hi"] - row["lo"]
    if span <= 0:
        return 30.0
    return float((row["f1"] - row["f0"]) / span)