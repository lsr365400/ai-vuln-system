"""PentAGI-style three-layer memory system.

Layer 1 — Working Memory: per-session state (in-memory)
Layer 2 — Episodic Memory: attack chains in SQLite
Layer 3 — Long-term Memory: vector embeddings via SiliconFlow API + cosine search
"""

import asyncio
import hashlib
import json
import logging
import math
import time
from pathlib import Path

import aiosqlite
import httpx

logger = logging.getLogger(__name__)

SIMILARITY_THRESHOLD = 0.75

_settings = None


def _get_settings():
    global _settings
    if _settings is None:
        from src.config import load_settings
        _settings = load_settings()
    return _settings

_embedding_last_call = 0.0
_embedding_min_interval = 0.5


# ---------------------------------------------------------------------------
# Layer 1: Working Memory
# ---------------------------------------------------------------------------

class WorkingMemory:
    def __init__(self):
        self.phase = "recon"
        self.discovered_endpoints: list[str] = []
        self.active_credentials: dict[str, str] = {}
        self.waf_detected = ""
        self.tech_stack_hints: list[str] = []
        self.pending_targets: list[str] = []
        self.dead_ends: list[str] = []
        self.findings: list[dict] = []

    def snapshot(self) -> dict:
        return {
            "phase": self.phase, "endpoints": len(self.discovered_endpoints),
            "waf": self.waf_detected, "tech": self.tech_stack_hints[:5],
            "pending": len(self.pending_targets), "findings": len(self.findings),
        }


# ---------------------------------------------------------------------------
# Layer 2: Episodic Memory (attack chains)
# ---------------------------------------------------------------------------

async def record_attack_chain(db: aiosqlite.Connection, session_id: str,
                               from_node: str, relationship: str, to_node: str) -> None:
    await db.execute(
        "INSERT INTO attack_chains (session_id, from_node, relationship, to_node) VALUES (?,?,?,?)",
        (session_id, from_node, relationship, to_node),
    )
    await db.commit()


async def get_attack_chains(db: aiosqlite.Connection, limit: int = 20) -> list[dict]:
    cursor = await db.execute(
        "SELECT from_node, relationship, to_node FROM attack_chains ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    rows = await cursor.fetchall()
    cols = [c[0] for c in cursor.description]
    return [dict(zip(cols, r)) for r in rows]


# ---------------------------------------------------------------------------
# Layer 3: Vector Memory
# ---------------------------------------------------------------------------

async def _get_embedding(text: str) -> list[float]:
    global _embedding_last_call
    elapsed = time.time() - _embedding_last_call
    if elapsed < _embedding_min_interval:
        await asyncio.sleep(_embedding_min_interval - elapsed)
    s = _get_settings()
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            s.embedding_api_url,
            headers={"Authorization": f"Bearer {s.embedding_api_key}", "Content-Type": "application/json"},
            json={"model": s.embedding_model, "input": text[:8000]},
        )
        resp.raise_for_status()
        _embedding_last_call = time.time()
        return resp.json()["data"][0]["embedding"]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    ma = math.sqrt(sum(x * x for x in a))
    mb = math.sqrt(sum(x * x for x in b))
    return dot / (ma * mb) if ma and mb else 0.0


async def store_vector(db: aiosqlite.Connection, content: str,
                       mem_type: str = "finding", metadata: dict = None) -> str:
    embedding = await _get_embedding(content)
    memo_id = hashlib.sha256(content.encode()).hexdigest()[:12]
    await db.execute(
        "INSERT OR REPLACE INTO vector_memory (id, content, embedding_json, mem_type, metadata_json) VALUES (?,?,?,?,?)",
        (memo_id, content, json.dumps(embedding), mem_type, json.dumps(metadata or {}, ensure_ascii=False)),
    )
    await db.commit()
    return memo_id


async def search_vectors(db: aiosqlite.Connection, query: str, mem_type: str = None,
                         top_k: int = 5) -> list[dict]:
    query_vec = await _get_embedding(query)
    where = f"WHERE mem_type = '{mem_type}'" if mem_type else ""
    cursor = await db.execute(f"SELECT content, embedding_json, mem_type, metadata_json FROM vector_memory {where}")
    results = []
    async for row in cursor:
        content, emb_json, mtype, meta_json = row
        try:
            sim = _cosine(query_vec, json.loads(emb_json))
            if sim >= SIMILARITY_THRESHOLD:
                results.append({"content": content, "similarity": round(sim, 3), "mem_type": mtype})
        except Exception:
            pass
    results.sort(key=lambda x: x["similarity"], reverse=True)
    return results[:top_k]


async def build_cross_target_context(db: aiosqlite.Connection, target_desc: str) -> str:
    parts = []
    similar = await search_vectors(db, target_desc, "finding", top_k=3)
    if similar:
        parts.append("## 语义相似的历史发现\n" + "\n".join(
            f"- {s['content'][:200]} (相似度:{s['similarity']})" for s in similar))
    chains = await get_attack_chains(db, limit=5)
    if chains:
        parts.append("## 历史攻击链模式\n" + "\n".join(
            f"- {c['from_node']} → [{c['relationship']}] → {c['to_node']}" for c in chains))
    return "\n\n".join(parts) if parts else ""
