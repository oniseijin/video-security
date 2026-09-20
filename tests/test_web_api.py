from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from video_security.config import Config
from video_security.db import connect, init_db
from video_security.geo import ensure_table
from video_security.web.server import WebServer

JPEG = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffdb004300ffd9")


def _seed(base: Path) -> None:
    db_path = base / "t.db"
    conn = connect(str(db_path))
    init_db(conn)
    ensure_table(conn)
    front = str(base / "clips" / "NORMAL" / "front" / "a.mp4")
    rear = str(base / "clips" / "NORMAL" / "rear" / "a.mp4")
    session_json = json.dumps(
        [{"channel": "front", "path": front}, {"channel": "rear", "path": rear}]
    )
    conn.executescript(
        f"""
        INSERT INTO jobs (id, video_path, video_hash, status, recording_start_utc,
                          import_id, created_at)
        VALUES
        (1, '{front}', 'h1', 'done', '2025-09-22 02:57:00', 'imp-1',
         '2025-09-22 03:00:00'),
        (2, '{rear}', 'h2', 'done', '2025-09-22 02:57:00', 'imp-1',
         '2025-09-22 03:00:00'),
        (3, '{base}/clips/PARKING/front/b.mp4', 'h3', 'done',
         '2025-08-01 10:00:00', 'imp-1', '2026-09-20 01:00:00'),
        (4, '{base}/clips/PARKING/rear/b.mp4', 'h4', 'done',
         '2025-08-01 10:00:00', 'imp-1', '2026-09-20 01:00:00');
        INSERT INTO clips (job_id, clip_id, filename, channel, priority,
                           recording_start_utc, duration_sec, lighting, has_audio)
        VALUES
        (1, 0, 'a.mp4', 'front', 0.3, '2025-09-22 02:57:00', 120.0, 'day', 1),
        (2, 0, 'a.mp4', 'rear', 0.3, '2025-09-22 02:57:00', 120.0, 'day', 1),
        (3, 0, 'b.mp4', 'front', 0.8, '2025-08-01 10:00:00', 60.0, 'day', 1),
        (4, 0, 'b.mp4', 'rear', 0.8, '2025-08-01 10:00:00', 60.0, 'day', 1);
        INSERT INTO sessions (job_id, session_id, clips_json)
        VALUES (1, 's1', '{session_json}'),
               (2, 's1', '{session_json}');
        INSERT INTO events (id, job_id, event_type, start_sec, end_sec, clip_id,
                            track_id, keyframes_json, faces_json, detector_score,
                            priority, status)
        VALUES
        (10, 1, 'plate_capture', 32.0, 33.0, 0, 7, '[]',
         '[[[0.1, 0.2, 0.3, 0.4]]]', 1.0, 0.6, 'detailed'),
        (11, 1, 'intrusion', 48.0, 50.0, 0, 8, '[]', NULL, 0.9, 0.9, 'pending');
        INSERT INTO vehicle_tracks (job_id, track_id, clip_id, first_frame,
                                    last_frame, weaving_score, direction, strip_json)
        VALUES (1, 7, 0, 840, 1800, NULL, 'W',
                '["{base}/artifacts/frames/1/track_7_0.jpg"]');
        INSERT INTO plates (job_id, track_id, clip_id, raw_text, norm_text,
                            confidence, best_frame, crop_path)
        VALUES
        (1, 7, 0, '習志野5001', '習志野5001', 1.0, 960,
         '{base}/artifacts/plates/1/track_7.jpg'),
        (1, 8, 0, 'Y2', 'Y2', 0.9, 1500, NULL);
        INSERT INTO clip_gps_data (job_id, clip_id, time_sec, lat, lon, speed_kmh,
                                  bearing, ax, ay, az)
        VALUES (1, 0, 0.0, 35.645, 140.045, 40.0, 90.0, 0.0, 0.0, 1.0),
               (1, 0, 30.0, 35.647, 140.046, 42.0, 90.0, 0.0, 0.0, 1.0),
               (1, 0, 60.0, 35.650, 140.047, 38.0, 90.0, 0.0, 0.0, 1.0);
        INSERT INTO transcript_segments (job_id, clip_id, segment_id, start_time,
                                         end_time, text, language)
        VALUES (1, 0, 1, 30.0, 35.0, 'おはよう', 'ja');
        INSERT INTO frame_text (job_id, clip_id, frame_number, text, text_kind,
                                region_json, confidence)
        VALUES (1, 0, 500, '千葉市立公園', 'scene', NULL, 0.9);
        INSERT INTO frames (job_id, clip_id, frame_number, timestamp_sec, dhash,
                             lighting_condition)
        VALUES (1, 0, 900, 30.0, 1, 'day'), (1, 0, 2700, 90.0, 2, 'day');
        """
    )
    conn.commit()
    conn.close()
    art = base / "artifacts"
    frames = art / "frames" / "1"
    plates = art / "plates" / "1"
    frames.mkdir(parents=True)
    plates.mkdir(parents=True)
    (frames / "event_10_0.jpg").write_bytes(JPEG)
    (frames / "event_10_0_raw.jpg").write_bytes(JPEG)
    (frames / "track_7_0.jpg").write_bytes(JPEG)
    (plates / "track_7.jpg").write_bytes(JPEG)
    conn = connect(str(db_path))
    conn.execute(
        "UPDATE events SET keyframes_json = ? WHERE id = 10",
        (json.dumps([str(frames / "event_10_0.jpg")]),),
    )
    conn.commit()
    conn.close()


@pytest.fixture()
def base_url(tmp_path: Path) -> Iterator[str]:
    _seed(tmp_path)
    cfg = Config()
    cfg.storage.db_path = str(tmp_path / "t.db")
    cfg.storage.artifact_dir = str(tmp_path / "artifacts")
    server = WebServer(("127.0.0.1", 0), cfg)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{int(server.server_address[1])}"
    server.shutdown()
    server.server_close()


def _get_json(url: str) -> Any:
    with urllib.request.urlopen(urllib.parse.quote(url, safe="/:?=&")) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _status_of(url: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(
            urllib.parse.quote(url, safe="/:?=&")
        ) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def test_jobs_list_and_filters(base_url: str) -> None:
    data = _get_json(f"{base_url}/api/jobs")
    assert data["total"] == 4
    by_id = {item["id"]: item for item in data["items"]}
    assert by_id[1]["channel"] == "front"
    assert by_id[1]["mode"] == "NORMAL"
    assert by_id[1]["pair_job_id"] == 2
    assert by_id[2]["pair_job_id"] == 1
    assert by_id[3]["mode"] == "PARKING"
    assert by_id[3]["pair_job_id"] == 4
    assert by_id[4]["pair_job_id"] == 3
    assert by_id[3]["archive"] is True
    assert by_id[1]["archive"] is False
    assert by_id[1]["has_gps"] is True
    assert by_id[3]["has_gps"] is False
    assert by_id[1]["counts"] == {"events": 2, "plates": 2, "faces": 1}

    pending = _get_json(f"{base_url}/api/jobs?mode=PARKING")
    assert [item["id"] for item in pending["items"]] == [4, 3]

    chan = _get_json(f"{base_url}/api/jobs?channel=rear")
    assert {item["id"] for item in chan["items"]} == {2, 4}

    q = _get_json(f"{base_url}/api/jobs?q=a.mp4")
    assert {item["id"] for item in q["items"]} == {1, 2}

    page = _get_json(f"{base_url}/api/jobs?limit=2&offset=2&sort=id&order=asc")
    assert page["total"] == 4
    assert [item["id"] for item in page["items"]] == [3, 4]


def test_job_detail(base_url: str) -> None:
    data = _get_json(f"{base_url}/api/jobs/1")
    assert data["pair"] == {"job_id": 2, "channel": "rear"}
    assert data["event_types"] == {"plate_capture": 1, "intrusion": 1}
    assert data["counts"]["plates"] == 2
    assert data["has_transcript"] is True
    assert data["duration_sec"] == 120.0


def test_job_events_category_and_faces(base_url: str) -> None:
    data = _get_json(f"{base_url}/api/jobs/1/events")
    items = {item["event_id"]: item for item in data["items"]}
    assert items[10]["category"] == "driving"
    assert items[10]["face_count"] == 1
    assert items[10]["description_source"] == "synthesized"
    assert items[10]["plate_norm"] == "習志野5001"
    assert items[10]["recorded_at"] is not None
    driving = _get_json(f"{base_url}/api/jobs/1/events?category=driving")
    assert len(driving["items"]) == 2


def test_events_cross_job(base_url: str) -> None:
    data = _get_json(f"{base_url}/api/events?category=driving")
    assert data["total"] == 2
    one = _get_json(f"{base_url}/api/events?limit=1")
    assert one["limit"] == 1
    assert len(one["items"]) == 1


def test_event_detail(base_url: str) -> None:
    data = _get_json(f"{base_url}/api/events/10")
    assert data["keyframes"][0]["url"] == "/media/frames/1/event_10_0.jpg"
    assert data["keyframes"][0]["raw_url"] == "/media/frames/1/event_10_0_raw.jpg"
    assert data["keyframes"][0]["faces"] == [[0.1, 0.2, 0.3, 0.4]]
    assert data["plates"][0]["norm_text"] == "習志野5001"
    assert data["plates"][0]["ken"] == "千葉県"
    assert data["plates"][0]["crop_url"] == "/media/plates/1/track_7.jpg"
    assert data["track"]["track_id"] == 7
    assert data["track"]["strip"] == ["/media/frames/1/track_7_0.jpg"]
    assert data["location"]["lat"] == pytest.approx(35.647)
    assert data["transcript_window"][0]["text"] == "おはよう"
    assert data["links"]["report"] == "/jobs/1/report#event-10"


def test_missing_event_404(base_url: str) -> None:
    status, body = _status_of(f"{base_url}/api/events/999")
    assert status == 404
    assert json.loads(body)["error"] == "event not found"


def test_categories(base_url: str) -> None:
    data = _get_json(f"{base_url}/api/categories")
    assert data["categories"]["driving"] == 2
    assert data["types"]["plate_capture"] == 1


def test_job_plates(base_url: str) -> None:
    data = _get_json(f"{base_url}/api/jobs/1/plates")
    items = {item["track_id"]: item for item in data["items"]}
    assert items[7]["ken"] == "千葉県"
    assert items[7]["crop_url"] == "/media/plates/1/track_7.jpg"
    assert items[7]["read_at_sec"] is not None
    assert items[7]["event_id"] == 10
    assert items[8]["crop_url"] is None


def test_plates_gallery_and_detail(base_url: str) -> None:
    data = _get_json(f"{base_url}/api/plates")
    assert data["total"] == 2
    by_norm = {item["norm_text"]: item for item in data["items"]}
    assert by_norm["習志野5001"]["best_crop_url"] == "/media/plates/1/track_7.jpg"
    assert by_norm["習志野5001"]["jobs"] == [1]
    detail = _get_json(f"{base_url}/api/plates/習志野5001")
    assert detail["ken"] == "千葉県"
    assert detail["sightings"][0]["job_id"] == 1
    assert detail["sightings"][0]["event_id"] == 10
    missing = _status_of(f"{base_url}/api/plates/ZZZ")
    assert missing[0] == 404


def test_job_tracks(base_url: str) -> None:
    data = _get_json(f"{base_url}/api/jobs/1/tracks")
    tr = data["items"][0]
    assert tr["track_id"] == 7
    assert tr["plate_norm"] == "習志野5001"
    assert tr["event_ids"] == [10]
    assert tr["strip"] == ["/media/frames/1/track_7_0.jpg"]


def test_job_gps(base_url: str) -> None:
    data = _get_json(f"{base_url}/api/jobs/1/gps")
    assert len(data["points"]) == 3
    assert data["points"][0]["lat"] == pytest.approx(35.645)
    assert data["events"][0]["event_id"] in (10, 11)


def test_search_japanese(base_url: str) -> None:
    data = _get_json(f"{base_url}/api/search?q=千葉市立公園")
    assert data["text"][0]["text"] == "千葉市立公園"
    data2 = _get_json(f"{base_url}/api/search?q=5001")
    assert data2["plates"][0]["norm_text"] == "習志野5001"
    data3 = _get_json(f"{base_url}/api/search?q=おはよう")
    assert data3["transcripts"][0]["text"] == "おはよう"
    status, body = _status_of(f"{base_url}/api/search?q=")
    assert status == 400
    bad = _status_of(f"{base_url}/api/search?q=%22%25%29")
    assert bad[0] == 400


def test_map_recent(base_url: str) -> None:
    data = _get_json(f"{base_url}/api/map/recent")
    ids = [item["event_id"] for item in data["items"]]
    assert 11 in ids


def test_media_serves_artifacts(base_url: str) -> None:
    status, _ = _status_of(f"{base_url}/media/frames/1/event_10_0.jpg")
    assert status == 200
    status, body = _status_of(f"{base_url}/media/plates/1/track_7.jpg")
    assert status == 200
    assert body == JPEG
    status, _ = _status_of(f"{base_url}/media/frames/1/nope.jpg")
    assert status == 404
    status, _ = _status_of(f"{base_url}/media/frames/1/..%2f..%2f..%2ft.db")
    assert status == 404


def test_report_html_embed(base_url: str) -> None:
    status, body = _status_of(f"{base_url}/api/jobs/1/report.html?embed=1")
    assert status == 200
    assert b"/media/frames/1/event_10_0.jpg" in body
    assert b'id="theme-toggle"' not in body
    assert b'id="face-toggle"' not in body
    status, body = _status_of(f"{base_url}/api/jobs/1/report.html")
    assert status == 200
    assert b'id="theme-toggle"' in body
    status, _ = _status_of(f"{base_url}/api/jobs/999/report.html")
    assert status in (404, 500)


def test_report_404_is_json(base_url: str) -> None:
    status, body = _status_of(f"{base_url}/api/jobs/999")
    assert status == 404
    assert json.loads(body)["error"] == "job not found"
