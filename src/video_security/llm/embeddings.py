from __future__ import annotations

import json
import sqlite3
import struct
from typing import Any

import numpy as np

from video_security.llm import LLMClient
from video_security.llm.ollama import OllamaError


def embed_query(client: LLMClient, model: str, text: str) -> list[float]:
    return client.embed(model, text)


def _event_text(conn: sqlite3.Connection, event_id: int) -> str | None:
    row = conn.execute(
        "SELECT id, llm_result_id FROM events WHERE id = ?", (event_id,)
    ).fetchone()
    if row is None:
        return None
    if row["llm_result_id"] is not None:
        ar = conn.execute(
            "SELECT raw_response FROM analysis_results WHERE id = ?",
            (row["llm_result_id"],),
        ).fetchone()
        if ar is not None:
            try:
                resp = json.loads(ar["raw_response"])
            except (json.JSONDecodeError, TypeError):
                pass
            else:
                desc = str(resp.get("description", ""))
                if desc:
                    return desc
    evt = conn.execute(
        "SELECT id, event_type FROM events WHERE id = ?", (event_id,)
    ).fetchone()
    if evt is None:
        return None
    return str(evt["event_type"])


def embed_events(
    client: LLMClient,
    model: str,
    conn: sqlite3.Connection,
    refresh: bool = False,
) -> int:
    embedded_count = 0
    rows = conn.execute(
        "SELECT id FROM events WHERE status = 'detailed' ORDER BY id"
    ).fetchall()
    for row in rows:
        event_id = int(row["id"])
        if not refresh:
            exists = conn.execute(
                "SELECT 1 FROM event_embeddings WHERE event_id = ? AND model = ?",
                (event_id, model),
            ).fetchone()
            if exists is not None:
                continue
        text = _event_text(conn, event_id)
        if text is None:
            continue
        try:
            embedding = embed_query(client, model, text)
        except OllamaError:
            continue
        blob = struct.pack(f"{len(embedding)}f", *embedding)
        conn.execute(
            "INSERT OR REPLACE INTO event_embeddings (event_id, embedding, model) "
            "VALUES (?, ?, ?)",
            (event_id, blob, model),
        )
        conn.commit()
        embedded_count += 1
    return embedded_count


def embed_transcripts(
    client: LLMClient,
    model: str,
    conn: sqlite3.Connection,
    refresh: bool = False,
) -> int:
    embedded_count = 0
    rows = conn.execute(
        "SELECT job_id, clip_id, segment_id, start_time, end_time, text "
        "FROM transcript_segments ORDER BY job_id, segment_id"
    ).fetchall()
    for row in rows:
        job_id = int(row["job_id"])
        clip_id = int(row["clip_id"])
        segment_id = int(row["segment_id"])
        if not refresh:
            exists = conn.execute(
                "SELECT 1 FROM transcript_embeddings "
                "WHERE job_id = ? AND clip_id = ? AND segment_id = ? "
                "AND model = ?",
                (job_id, clip_id, segment_id, model),
            ).fetchone()
            if exists is not None:
                continue
        text = str(row["text"])
        if not text.strip():
            continue
        try:
            embedding = embed_query(client, model, text)
        except OllamaError:
            continue
        blob = struct.pack(f"{len(embedding)}f", *embedding)
        conn.execute(
            "INSERT OR REPLACE INTO transcript_embeddings "
            "(job_id, clip_id, segment_id, start_time, end_time, text, "
            "embedding, model) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                job_id,
                clip_id,
                segment_id,
                row["start_time"],
                row["end_time"],
                text,
                blob,
                model,
            ),
        )
        conn.commit()
        embedded_count += 1
    return embedded_count


def semantic_search_transcripts(
    conn: sqlite3.Connection,
    query_embedding: list[float],
    top_k: int = 10,
    *,
    model: str,
) -> list[dict[str, Any]]:
    qvec = np.array(query_embedding, dtype=np.float32)
    q_norm = float(np.linalg.norm(qvec))
    if q_norm == 0:
        return []
    qvec_norm = qvec / q_norm
    rows = conn.execute(
        "SELECT id, embedding, job_id, segment_id, start_time, end_time, text "
        "FROM transcript_embeddings WHERE model = ?",
        (model,),
    ).fetchall()
    if not rows:
        return []
    entries: list[tuple[int, np.ndarray, sqlite3.Row]] = []
    for row in rows:
        blob: bytes = row["embedding"]
        dim = len(blob) // 4
        vec = np.array(struct.unpack(f"{dim}f", blob), dtype=np.float32)
        n = float(np.linalg.norm(vec))
        if n == 0:
            continue
        entries.append((int(row["id"]), vec / n, row))
    if not entries:
        return []
    stack = np.stack([e[1] for e in entries])
    similarities = np.dot(stack, qvec_norm)
    top_indices = np.argsort(similarities)[::-1][:top_k]
    results: list[dict[str, Any]] = []
    for idx in top_indices:
        _rid, _vec, row = entries[idx]
        results.append(
            {
                "job_id": int(row["job_id"]),
                "segment_id": int(row["segment_id"]),
                "start_time": float(row["start_time"]),
                "end_time": float(row["end_time"]),
                "text": str(row["text"])[:200],
                "score": round(float(similarities[idx]), 4),
            }
        )
    return results


def get_all_embeddings(
    conn: sqlite3.Connection, model: str
) -> list[tuple[int, np.ndarray]]:
    rows = conn.execute(
        "SELECT event_id, embedding FROM event_embeddings WHERE model = ?",
        (model,),
    ).fetchall()
    out: list[tuple[int, np.ndarray]] = []
    for row in rows:
        blob: bytes = row["embedding"]
        dim = len(blob) // 4
        arr = np.array(struct.unpack(f"{dim}f", blob), dtype=np.float32)
        out.append((int(row["event_id"]), arr))
    return out


def semantic_search(
    conn: sqlite3.Connection,
    query_embedding: list[float],
    top_k: int = 10,
    *,
    model: str,
) -> list[dict[str, Any]]:
    qvec = np.array(query_embedding, dtype=np.float32)
    q_norm = float(np.linalg.norm(qvec))
    if q_norm == 0:
        return []
    qvec_norm = qvec / q_norm
    candidates = get_all_embeddings(conn, model)
    if not candidates:
        return []
    ids: list[int] = []
    vecs: list[np.ndarray] = []
    for eid, ev in candidates:
        n = float(np.linalg.norm(ev))
        if n == 0:
            continue
        ids.append(eid)
        vecs.append(ev / n)
    if not ids:
        return []
    stack = np.stack(vecs)
    similarities = np.dot(stack, qvec_norm)
    top_indices = np.argsort(similarities)[::-1][:top_k]
    results: list[dict[str, Any]] = []
    for idx in top_indices:
        eid = ids[idx]
        score = float(similarities[idx])
        evt = conn.execute(
            "SELECT id, event_type, start_sec FROM events WHERE id = ?", (eid,)
        ).fetchone()
        if evt is None:
            continue
        snippet = _event_text(conn, eid) or str(evt["event_type"])
        results.append(
            {
                "event_id": eid,
                "score": round(score, 4),
                "snippet": snippet[:200],
            }
        )
    return results