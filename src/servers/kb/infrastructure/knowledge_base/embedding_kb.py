"""
embedding_kb.py — build/load KB embeddings (hybrid keyword + semantic)
======================================================================
Lives in the knowledge_base folder alongside the .jsonl files.
Embeds each entry once, caches vectors to disk, rebuilds only when the KB
content changes (hash check). Embeds short_answer + question + aliases + tags
so both the canonical answer and caller phrasings contribute to similarity.
"""
import json as _json
import os as _os
import hashlib as _hashlib
from pathlib import Path as _Path
import numpy as _np

_KB_DIR     = _Path(__file__).resolve().parent
_CACHE_PATH = _KB_DIR / "kb_vectors.npz"
_EMBED_MODEL_NAME = _os.environ.get("KB_EMBED_MODEL", "BAAI/bge-large-en-v1.5")

try:
    from sentence_transformers import SentenceTransformer as _SentenceTransformer
    _embed_model = _SentenceTransformer(_EMBED_MODEL_NAME)
    print(f"[mcp] Loaded embedding model: {_EMBED_MODEL_NAME}")
except Exception as e:
    _embed_model = None
    print(f"[mcp] Error loading embedding model: {e}")


def _load_kb():
    """Read every *.jsonl in the KB folder; one JSON object per line."""
    entries = []
    if not _KB_DIR.exists():
        print(f"[mcp] WARNING: KB dir {_KB_DIR} not found")
        return entries
    for f in sorted(_KB_DIR.glob("*.jsonl")):
        with open(f, encoding="utf-8") as fh:
            for i, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    e = _json.loads(line)
                    if e.get("active", True):
                        entries.append(e)
                except _json.JSONDecodeError as ex:
                    print(f"[mcp] skip bad line {f.name}:{i} — {ex}")
    print(f"[mcp] loaded {len(entries)} active KB entries from {_KB_DIR}")
    return entries


_KB_ENTRIES = _load_kb()


def _entry_text(entry: dict) -> str:
    """
    Build the text to embed for ONE entry. Joins the fields that carry meaning:
    short_answer + question + aliases + tags. Handles tags being a list.
    """
    tags = entry.get("tags", [])
    if isinstance(tags, list):
        tags = " ".join(tags)
    parts = [
        entry.get("short_answer", ""),
        entry.get("question", ""),
        " ".join(entry.get("aliases", [])),
        tags,
    ]
    return " . ".join(p for p in parts if p)


def _embed_entries(entries: list):
    """Embed ALL entries → returns a normalized 2D array (one row per entry)."""
    if not _embed_model:
        print("[mcp] No embedding model available, skipping KB embedding")
        return None
    texts = [_entry_text(e) for e in entries]
    try:
        vectors = _embed_model.encode(texts, normalize_embeddings=True,
                                      show_progress_bar=True)
        print(f"[mcp] embedded {len(vectors)} KB entries with {_EMBED_MODEL_NAME}")
        return vectors
    except Exception as e:
        print(f"[mcp] Error embedding KB: {e}")
        return None


def _hash_entry(entries: list) -> str:
    """Hash of entry content + model name. Changes if the KB changes."""
    h = _hashlib.sha256()
    for e in entries:
        h.update(e.get("id", "").encode("utf-8"))
        h.update(_entry_text(e).encode("utf-8"))
    h.update(str(_EMBED_MODEL_NAME).encode("utf-8"))
    return h.hexdigest()


def _build_or_load_vector():
    """Load cached vectors if the hash matches; otherwise embed once and save."""
    if not _embed_model or not _KB_ENTRIES:
        return None
    kb_hash = _hash_entry(_KB_ENTRIES)

    if _CACHE_PATH.exists():
        try:
            cache = _np.load(_CACHE_PATH, allow_pickle=True)
            if str(cache["kb_hash"]) == kb_hash:
                print(f"[mcp] Loaded KB vectors from cache {_CACHE_PATH}")
                return cache["vectors"]
            print("[mcp] KB hash mismatch — rebuilding vectors")
        except Exception as e:
            print(f"[mcp] Error loading KB vector cache: {e}")

    vectors = _embed_entries(_KB_ENTRIES)
    if vectors is None:
        return None
    try:
        _np.savez_compressed(_CACHE_PATH, kb_hash=kb_hash, vectors=vectors)
        print(f"[mcp] Saved KB vectors to cache {_CACHE_PATH}")
    except Exception as e:
        print(f"[mcp] Error saving KB vector cache: {e}")
    return vectors


_KB_VECTORS = _build_or_load_vector()


def _tokenize(text: str) -> set:
    return set(text.lower().replace("?", " ").replace(",", " ")
                   .replace("/", " ").replace(".", " ").split())


def _semantic_score(query: str):
    """
    Cosine similarity of query vs every entry. Returns a 1D array aligned to
    _KB_ENTRIES (index i = entry i), or None if embeddings are unavailable.
    """
    if not _embed_model or _KB_VECTORS is None:
        print(f"[mcp] SEM FALLBACK model={_embed_model is not None} vectors={_KB_VECTORS is not None}")
        return None
    try:
        qv = _embed_model.encode([query], normalize_embeddings=True)[0]
        return _KB_VECTORS @ qv          # both normalized → dot = cosine
    except Exception as e:
        print(f"[mcp] Error computing semantic scores: {e}")
        return None


if __name__ == "__main__":
    # run directly to (re)build the cache:  python embedding_kb.py
    print("done." if _KB_VECTORS is not None else "no vectors built.")