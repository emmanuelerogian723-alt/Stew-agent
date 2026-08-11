"""
S.T.E.W Vector Memory — semantic recall across sessions.
Primary: ChromaDB (if installed). Fallback: numpy cosine similarity.
No sentence-transformers, no PyTorch — lightweight for Render free tier.
"""
import logging
import hashlib
import os
import json
from typing import Optional

logger = logging.getLogger(__name__)

_INITIALIZED = False
_BACKEND = None
_chroma_collection = None
_numpy_vectors = []

MAX_MEMORIES_PER_QUERY = 8
MIN_RELEVANCE_THRESHOLD = 0.25

CHROMA_PATH = os.getenv("CHROMA_DB_PATH", "/tmp/stew_chromadb")
NUMPY_PATH = os.getenv("STEW_MEMORY_PATH", "/tmp/stew_memory.json")


def _doc_id(user_id: str, content: str) -> str:
    return hashlib.md5(f"{user_id}:{content[:200]}".encode()).hexdigest()


def _hash_to_vector(text: str, dim: int = 256) -> list:
    """Hash-based vector embedding — no ML needed."""
    vec = [0.0] * dim
    words = text.lower().split()
    for word in words:
        h = int(hashlib.md5(word.encode()).hexdigest(), 16)
        vec[h % dim] += 1.0
    mag = sum(v * v for v in vec) ** 0.5
    if mag > 0:
        vec = [v / mag for v in vec]
    return vec


def _cosine_sim(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = sum(x * x for x in a) ** 0.5
    mag_b = sum(x * x for x in b) ** 0.5
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def _init_chromadb():
    global _chroma_collection, _BACKEND
    try:
        import chromadb
        from chromadb.utils import embedding_functions
        os.makedirs(CHROMA_PATH, exist_ok=True)
        client = chromadb.PersistentClient(path=CHROMA_PATH)
        _chroma_collection = client.get_or_create_collection(
            name="stew_memories",
            embedding_function=embedding_functions.DefaultEmbeddingFunction(),
            metadata={"hnsw:space": "cosine"},
        )
        _BACKEND = "chromadb"
        logger.info("ChromaDB initialized for vector memory")
        return True
    except ImportError:
        logger.info("ChromaDB not installed — using numpy fallback")
        return False
    except Exception as e:
        logger.warning(f"ChromaDB init failed: {e} — using numpy fallback")
        return False


def _init_numpy():
    global _BACKEND, _numpy_vectors
    _BACKEND = "numpy"
    try:
        if os.path.exists(NUMPY_PATH):
            with open(NUMPY_PATH, "r") as f:
                _numpy_vectors = json.load(f)
        logger.info(f"Numpy memory loaded: {len(_numpy_vectors)} memories")
    except Exception as e:
        logger.warning(f"Numpy memory load failed: {e}")
        _numpy_vectors = []


def _save_numpy():
    try:
        with open(NUMPY_PATH, "w") as f:
            json.dump(_numpy_vectors[-5000:], f)
    except Exception as e:
        logger.warning(f"Numpy memory save failed: {e}")


def _ensure_init():
    global _INITIALIZED
    if _INITIALIZED:
        return
    if not _init_chromadb():
        _init_numpy()
    _INITIALIZED = True


def store_memory(user_id: str, role: str, content: str,
                 platform: str = "api", conversation_id: Optional[str] = None,
                 metadata_extra: Optional[dict] = None) -> bool:
    _ensure_init()
    try:
        if _BACKEND == "chromadb" and _chroma_collection:
            meta = {"user_id": user_id, "role": role, "platform": platform,
                    "conversation_id": conversation_id or ""}
            if metadata_extra:
                meta.update(metadata_extra)
            _chroma_collection.upsert(
                ids=[_doc_id(user_id, content)],
                documents=[content[:2000]],
                metadatas=[meta],
            )
            return True
        else:
            _numpy_vectors.append({
                "id": _doc_id(user_id, content),
                "user_id": user_id, "role": role,
                "platform": platform, "conversation_id": conversation_id or "",
                "content": content[:2000],
                "vector": _hash_to_vector(content),
            })
            if len(_numpy_vectors) % 10 == 0:
                _save_numpy()
            return True
    except Exception as e:
        logger.warning(f"store_memory error: {e}")
        return False


def recall_relevant(user_id: str, query: str, platform: Optional[str] = None,
                    n_results: int = MAX_MEMORIES_PER_QUERY) -> list:
    _ensure_init()
    try:
        if _BACKEND == "chromadb" and _chroma_collection:
            where = {"user_id": user_id}
            if platform:
                where = {"$and": [{"user_id": user_id}, {"platform": platform}]}
            results = _chroma_collection.query(
                query_texts=[query[:2000]], n_results=n_results,
                where=where, include=["documents", "metadatas", "distances"],
            )
            memories = []
            if results and results.get("documents"):
                for doc, meta, dist in zip(
                    results["documents"][0], results["metadatas"][0], results["distances"][0]
                ):
                    memories.append({
                        "content": doc, "role": meta.get("role", "unknown"),
                        "platform": meta.get("platform", "unknown"),
                        "conversation_id": meta.get("conversation_id", ""),
                        "distance": round(dist, 3),
                    })
            return memories
        else:
            query_vec = _hash_to_vector(query)
            user_memories = [m for m in _numpy_vectors if m["user_id"] == user_id]
            if platform:
                user_memories = [m for m in user_memories if m["platform"] == platform]
            scored = []
            for m in user_memories:
                sim = _cosine_sim(query_vec, m["vector"])
                if sim >= MIN_RELEVANCE_THRESHOLD:
                    scored.append((sim, m))
            scored.sort(key=lambda x: -x[0])
            return [{
                "content": m["content"], "role": m["role"],
                "platform": m["platform"],
                "conversation_id": m.get("conversation_id", ""),
                "distance": round(1 - sim, 3),
            } for sim, m in scored[:n_results]]
    except Exception as e:
        logger.warning(f"recall_relevant error: {e}")
        return []


def format_memories_for_prompt(memories: list) -> str:
    if not memories:
        return ""
    lines = ["\n\n=== RELEVANT PAST CONTEXT (from memory) ==="]
    for i, m in enumerate(memories, 1):
        role_label = "You said" if m["role"] == "assistant" else "User said"
        lines.append(f"{i}. {role_label}: {m['content'][:300]}")
    lines.append("=== END PAST CONTEXT ===\n")
    return "\n".join(lines)


def get_user_memory_summary(user_id: str, limit: int = 50) -> dict:
    _ensure_init()
    try:
        if _BACKEND == "chromadb" and _chroma_collection:
            result = _chroma_collection.get(where={"user_id": user_id}, limit=limit)
            return {"enabled": True, "backend": "chromadb", "count": len(result.get("ids", []))}
        else:
            count = sum(1 for m in _numpy_vectors if m["user_id"] == user_id)
            return {"enabled": True, "backend": "numpy", "count": count}
    except Exception as e:
        return {"enabled": False, "count": 0, "error": str(e)}


def clear_user_memories(user_id: str) -> int:
    _ensure_init()
    try:
        if _BACKEND == "chromadb" and _chroma_collection:
            result = _chroma_collection.get(where={"user_id": user_id})
            ids = result.get("ids", [])
            if ids:
                _chroma_collection.delete(ids=ids)
            return len(ids)
        else:
            before = len(_numpy_vectors)
            _numpy_vectors[:] = [m for m in _numpy_vectors if m["user_id"] != user_id]
            _save_numpy()
            return before - len(_numpy_vectors)
    except Exception as e:
        logger.warning(f"clear_user_memories error: {e}")
        return 0
