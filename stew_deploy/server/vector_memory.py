"""
S.T.E.W Vector Memory — ChromaDB-based semantic memory for cross-session recall.
Stores conversation embeddings per user + platform (api/telegram).
Retrieves relevant past context before each response.
"""
import logging
import hashlib
import os
from typing import Optional

logger = logging.getLogger(__name__)

# Lazy-loaded singletons
_chroma_client = None
_collection = None
_embed_func = None
_INITIALIZED = False

# Storage path — persistent on Render
DB_PATH = os.getenv("CHROMA_DB_PATH", "/tmp/stew_chromadb")
COLLECTION_NAME = "stew_memories"

MAX_MEMORIES_PER_QUERY = 8
MIN_RELEVANCE_DISTANCE = 0.75  # Lower = more relevant (ChromaDB uses cosine distance)


def _get_or_create_client():
    """Lazy-init ChromaDB client (in-process, no external server)."""
    global _chroma_client, _collection, _embed_func, _INITIALIZED
    if _INITIALIZED:
        return _chroma_client, _collection, _embed_func

    try:
        import chromadb
        from chromadb.utils import embedding_functions

        os.makedirs(DB_PATH, exist_ok=True)
        _chroma_client = chromadb.PersistentClient(path=DB_PATH)

        # Use sentence-transformers for free, local embeddings
        # Falls back to chromadb's default ONNX embedder if sentence-transformers not available
        try:
            _embed_func = embedding_functions.SentenceTransformerEmbeddingFunction(
                model_name="all-MiniLM-L6-v2"
            )
            logger.info("Using sentence-transformers for embeddings (all-MiniLM-L6-v2)")
        except Exception as e:
            logger.warning(f"sentence-transformers unavailable, using default: {e}")
            _embed_func = embedding_functions.DefaultEmbeddingFunction()

        _collection = _chroma_client.get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=_embed_func,
            metadata={"hnsw:space": "cosine"},
        )
        _INITIALIZED = True
        logger.info(f"ChromaDB initialized at {DB_PATH}, collection: {COLLECTION_NAME}")
        return _chroma_client, _collection, _embed_func

    except ImportError:
        logger.error("chromadb not installed — vector memory disabled")
        return None, None, None
    except Exception as e:
        logger.error(f"ChromaDB init failed: {e}")
        return None, None, None


def _doc_id(user_id: str, content: str) -> str:
    """Stable ID from user_id + content hash."""
    return hashlib.md5(f"{user_id}:{content[:200]}".encode()).hexdigest()


def store_memory(
    user_id: str,
    role: str,
    content: str,
    platform: str = "api",
    conversation_id: Optional[str] = None,
    metadata_extra: Optional[dict] = None,
) -> bool:
    """
    Store a conversation message as a vector embedding.
    Called after every user/assistant message.
    """
    _, collection, _ = _get_or_create_client()
    if not collection:
        return False

    try:
        meta = {
            "user_id": user_id,
            "role": role,
            "platform": platform,
            "conversation_id": conversation_id or "",
        }
        if metadata_extra:
            meta.update(metadata_extra)

        doc_id = _doc_id(user_id, content)
        collection.upsert(
            ids=[doc_id],
            documents=[content[:2000],],  # Truncate to keep embedding fast
            metadatas=[meta],
        )
        return True
    except Exception as e:
        logger.warning(f"store_memory error: {e}")
        return False


def recall_relevant(
    user_id: str,
    query: str,
    platform: Optional[str] = None,
    n_results: int = MAX_MEMORIES_PER_QUERY,
) -> list[dict]:
    """
    Retrieve semantically relevant past memories for this user.
    Filters by user_id, optionally by platform.
    Returns list of {content, role, platform, conversation_id, distance}.
    """
    _, collection, _ = _get_or_create_client()
    if not collection:
        return []

    try:
        where_clause = {"user_id": user_id}
        if platform:
            where_clause = {"$and": [{"user_id": user_id}, {"platform": platform}]}

        results = collection.query(
            query_texts=[query[:2000]],
            n_results=n_results,
            where=where_clause,
            include=["documents", "metadatas", "distances"],
        )

        memories = []
        if not results or not results.get("documents"):
            return memories

        docs = results["documents"][0]
        metas = results["metadatas"][0]
        dists = results["distances"][0]

        for doc, meta, dist in zip(docs, metas, dists):
            if dist <= MIN_RELEVANCE_DISTANCE:  # Only include semantically relevant
                memories.append({
                    "content": doc,
                    "role": meta.get("role", "unknown"),
                    "platform": meta.get("platform", "unknown"),
                    "conversation_id": meta.get("conversation_id", ""),
                    "distance": round(dist, 3),
                })

        logger.info(f"Recalled {len(memories)} relevant memories for user {user_id[:8]}...")
        return memories

    except Exception as e:
        logger.warning(f"recall_relevant error: {e}")
        return []


def format_memories_for_prompt(memories: list[dict]) -> str:
    """Format recalled memories as context for the LLM system prompt."""
    if not memories:
        return ""

    lines = ["\n\n━━━ RELEVANT PAST CONTEXT (from memory) ━━━"]
    for i, m in enumerate(memories, 1):
        role_label = "You said" if m["role"] == "assistant" else "User said"
        lines.append(f"{i}. {role_label}: {m['content'][:300]}")
    lines.append("━━━ END PAST CONTEXT ━━━\n")

    return "\n".join(lines)


def get_user_memory_summary(user_id: str, limit: int = 50) -> dict:
    """Get a summary of all stored memories for a user (for debugging/dashboard)."""
    _, collection, _ = _get_or_create_client()
    if not collection:
        return {"enabled": False, "count": 0}

    try:
        result = collection.get(
            where={"user_id": user_id},
            limit=limit,
            include=["metadatas"],
        )
        return {
            "enabled": True,
            "count": len(result.get("ids", [])),
            "memories": [
                {"role": m.get("role"), "platform": m.get("platform")}
                for m in result.get("metadatas", [])
            ],
        }
    except Exception as e:
        return {"enabled": False, "count": 0, "error": str(e)}


def clear_user_memories(user_id: str) -> int:
    """Delete all memories for a user."""
    _, collection, _ = _get_or_create_client()
    if not collection:
        return 0

    try:
        result = collection.get(where={"user_id": user_id})
        ids = result.get("ids", [])
        if ids:
            collection.delete(ids=ids)
        return len(ids)
    except Exception as e:
        logger.warning(f"clear_user_memories error: {e}")
        return 0
