import dataclasses
import sqlite3
from pathlib import Path


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
]


def connect(db_path: str) -> sqlite3.Connection:
    parent = Path(db_path).expanduser().parent
    if str(parent) not in ("", "."):
        parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    current_version: int = conn.execute("PRAGMA user_version").fetchone()[0]
    for idx in range(current_version, len(MIGRATIONS)):
        conn.execute("BEGIN IMMEDIATE")
        for stmt in MIGRATIONS[idx]:
            conn.execute(stmt)
        conn.execute(f"PRAGMA user_version = {idx + 1}")
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


def update_job_status(conn: sqlite3.Connection, job_id: int, status: str) -> None:
    conn.execute(
        "UPDATE jobs SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (status, job_id),
    )
    conn.commit()


def list_jobs(conn: sqlite3.Connection) -> list[JobRow]:
    rows = conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()
    return [_row_to_job(r) for r in rows]