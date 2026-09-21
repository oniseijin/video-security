from __future__ import annotations

import json
import sqlite3
import struct
from typing import Any

import numpy as np

from video_security.llm.ollama import OllamaClient, OllamaError

EMBED_MODEL = "nomic-embed-text"


def embed_query(client: OllamaClient, model: str, text: str) -> list[float]:
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
    client: OllamaClient,
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


def get_all_embeddings(
    conn: sqlite3.Connection,
) -> list[tuple[int, np.ndarray]]:
    rows = conn.execute(
        "SELECT event_id, embedding FROM event_embeddings"
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
) -> list[dict[str, Any]]:
    qvec = np.array(query_embedding, dtype=np.float32)
    q_norm = float(np.linalg.norm(qvec))
    if q_norm == 0:
        return []
    qvec_norm = qvec / q_norm
    candidates = get_all_embeddings(conn)
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