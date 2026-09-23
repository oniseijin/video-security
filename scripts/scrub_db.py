import sqlite3
import sys
from pathlib import Path

from video_security.jp_plates import _PLACES

KEEP_CHARS = set("".join(_PLACES.keys()))


def redact_plate(text: str) -> str:
    return "".join(ch if ch in KEEP_CHARS else "■" for ch in text)


def scrub(src_db: str, dst_db: str) -> dict[str, int]:
    dst_path = Path(dst_db)
    if dst_path.exists():
        dst_path.unlink()
    src = sqlite3.connect(f"file:{src_db}?mode=ro", uri=True)
    out = sqlite3.connect(dst_path)
    src.backup(out)
    src.close()
    cur = out.cursor()
    counts: dict[str, int] = {}
    rows = cur.execute("SELECT rowid, raw_text, norm_text FROM plates").fetchall()
    for rowid, raw, norm in rows:
        cur.execute(
            "UPDATE plates SET raw_text = ?, norm_text = ? WHERE rowid = ?",
            (
                redact_plate(raw) if raw else raw,
                redact_plate(norm) if norm else norm,
                rowid,
            ),
        )
    counts["plates"] = len(rows)
    counts["frame_text"] = cur.execute(
        "UPDATE frame_text SET text = '[redacted]'"
    ).rowcount
    counts["transcripts"] = cur.execute(
        "UPDATE transcript_segments SET text = '[redacted]'"
    ).rowcount
    counts["geocode"] = cur.execute(
        "UPDATE gps_reverse_geocode SET description = '[redacted]'"
    ).rowcount
    counts["llm"] = cur.execute(
        "UPDATE analysis_results SET raw_response = '', tiled_results = NULL"
    ).rowcount
    out.commit()
    out.close()
    return counts


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: scrub_db.py <src-db> <dst-db>")
    src_db, dst_db = sys.argv[1], sys.argv[2]
    counts = scrub(src_db, dst_db)
    for name, count in counts.items():
        print(f"{name}: {count} rows scrubbed")


if __name__ == "__main__":
    main()
