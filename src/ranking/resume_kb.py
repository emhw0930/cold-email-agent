# ============================================================
#  resume_kb.py
#  A tiny retrieval layer (RAG) over the candidate's own experience.
#
#  Problem it solves: the cold-email writer used to get one flat bio for
#  every role, so an LLM-agent job and a computer-vision job were pitched
#  the same way. Here we turn the résumé into a small searchable knowledge
#  base of bullets and, per job description, surface the few MOST RELEVANT
#  bullets — so each email leads with the experience that fits that role,
#  drawn only from real, curated facts (an anti-fabrication guardrail).
#
#  How it works:
#    - Corpus lives in assets/experience.json (curated bullets + tags).
#    - Each bullet is embedded once (Gemini gemini-embedding-001) and the
#      768-dim vector is cached in the resume_chunks table of the committed
#      h1b_employers.db — so the daily/CI run never re-embeds the corpus.
#    - retrieve() embeds the job description as a QUERY and ranks bullets by
#      a HYBRID score: semantic cosine similarity + literal tag overlap.
#      Semantics catch meaning ("CV role" ~ "YOLOv5"); tags guarantee exact
#      tech-term recall ("Spring Boot" must match "Spring Boot").
#
#  Graceful degradation (matches the rest of the project's $0 ethos):
#    - No API key / embedding quota spent  -> keyword (tag-only) retrieval.
#    - No corpus / no matches              -> returns [] and the caller
#      falls back to the plain bio.
#
#  Vectors are stored as a JSON list of floats (pure-Python cosine, no numpy)
#  so this adds NO new dependency to the daily pipeline.
#
#  Public:
#    build_index(force=False) -> int      # (re)embed changed bullets; rows touched
#    retrieve(jd_text, title="", k=5) -> list[dict]   # [{id,text,source,score}]
# ============================================================

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from src.core import config
from src.core import gemini
from src.core import h1b_db

CORPUS_PATH = Path(config.PROJECT_ROOT) / "assets" / "experience.json"

# The knowledge base holds résumé-derived text + vectors, which are PERSONAL.
# It lives in its own gitignored DB — NOT the committed h1b_employers.db — so a
# public repo (or a fork) never publishes the owner's résumé content. The KB is
# rebuilt locally from assets/experience.json whenever it's missing.
KB_DB_PATH = str(Path(config.PROJECT_ROOT) / "data" / "resume_kb.db")

# Hybrid weights: semantic similarity dominates, tag overlap sharpens exact
# tech-term matches. Tuned for a small hand-curated corpus.
_W_SEMANTIC = 0.75
_W_TAG = 0.25

_SCHEMA = """
CREATE TABLE IF NOT EXISTS resume_chunks (
  id            TEXT PRIMARY KEY,
  text          TEXT NOT NULL,
  source        TEXT,
  tags          TEXT,            -- comma-joined
  embedding     TEXT,            -- JSON list[float]; NULL if not yet embedded
  content_hash  TEXT             -- sha256(text); '' when embedding is stale
);
"""


def _conn(db_path: str | None = None):
    conn = h1b_db.connect(db_path or KB_DB_PATH)
    conn.execute(_SCHEMA)
    return conn


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_corpus() -> list[dict]:
    """Read + normalize the curated experience bullets."""
    if not CORPUS_PATH.exists():
        return []
    raw = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    out = []
    for c in raw:
        text = (c.get("text") or "").strip()
        if not text:
            continue
        cid = c.get("id") or hashlib.sha1(text.encode()).hexdigest()[:12]
        tags = [t.strip().lower() for t in c.get("tags", []) if t.strip()]
        out.append({"id": cid, "text": text,
                    "source": (c.get("source") or "").strip(), "tags": tags})
    return out


# ── Build / refresh the index ────────────────────────────────
def build_index(force: bool = False, db_path: str | None = None) -> int:
    """Sync the resume_chunks table with assets/experience.json.

    Only bullets whose text changed (or all, if force) are re-embedded, so
    repeat calls are cheap. Rows for deleted bullets are removed. If the
    embedding API is unavailable, the text/tags are still stored (embedding
    NULL) so keyword retrieval keeps working; they'll be embedded next run.

    Returns the number of chunks embedded this call.
    """
    corpus = _load_corpus()
    conn = _conn(db_path)
    stored = {r["id"]: (r["content_hash"] or "")
              for r in conn.execute("SELECT id, content_hash FROM resume_chunks")}
    corpus_ids = {c["id"] for c in corpus}

    gone = [i for i in stored if i not in corpus_ids]
    if gone:
        conn.executemany("DELETE FROM resume_chunks WHERE id=?", [(i,) for i in gone])

    todo = [c for c in corpus if force or stored.get(c["id"]) != _hash(c["text"])]

    vectors: list[list[float] | None]
    if todo:
        try:
            vectors = gemini.embed([c["text"] for c in todo],
                                   task_type="RETRIEVAL_DOCUMENT")
        except gemini.GeminiUnavailable as e:
            print(f"  ⚠ embeddings unavailable ({e}); storing text only, "
                  f"keyword retrieval still works")
            vectors = [None] * len(todo)
    else:
        vectors = []

    embedded = 0
    for c, vec in zip(todo, vectors):
        conn.execute(
            "INSERT INTO resume_chunks (id, text, source, tags, embedding, content_hash) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET text=excluded.text, source=excluded.source, "
            "tags=excluded.tags, embedding=excluded.embedding, content_hash=excluded.content_hash",
            (c["id"], c["text"], c["source"], ",".join(c["tags"]),
             json.dumps(vec) if vec is not None else None,
             _hash(c["text"]) if vec is not None else ""))
        embedded += 1 if vec is not None else 0
    conn.commit()
    conn.close()
    return embedded


# ── Retrieval ────────────────────────────────────────────────
def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _tag_overlap(tags: list[str], text_low: str) -> float:
    """Fraction of a chunk's tags that appear literally in the query text."""
    if not tags:
        return 0.0
    return sum(1 for t in tags if t and t in text_low) / len(tags)


def retrieve(jd_text: str, title: str = "", k: int = 5,
             db_path: str | None = None) -> list[dict]:
    """Return the top-k most relevant résumé bullets for a job.

    Hybrid: 0.75 * semantic cosine + 0.25 * tag overlap. Degrades to tag-only
    ranking when the embedding API is unavailable. Returns
    [{id, text, source, score}] best-first; [] if nothing is relevant.
    """
    conn = _conn(db_path)
    rows = conn.execute(
        "SELECT id, text, source, tags, embedding FROM resume_chunks").fetchall()
    # First-run convenience: if the KB is empty, try to build it once.
    if not rows:
        conn.close()
        try:
            build_index(db_path=db_path)
        except Exception:
            return []
        conn = _conn(db_path)
        rows = conn.execute(
            "SELECT id, text, source, tags, embedding FROM resume_chunks").fetchall()
    conn.close()
    if not rows:
        return []

    text_low = f"{title} {jd_text}".lower()
    query = f"{title}\n{jd_text}".strip()[:2000]
    try:
        qvec = gemini.embed(query, task_type="RETRIEVAL_QUERY")[0] if query else None
    except gemini.GeminiUnavailable:
        qvec = None

    scored = []
    for r in rows:
        tags = [t for t in (r["tags"] or "").split(",") if t]
        tag_s = _tag_overlap(tags, text_low)
        emb = r["embedding"]
        if qvec and emb:
            sem = _cosine(qvec, json.loads(emb))
            score = _W_SEMANTIC * sem + _W_TAG * tag_s
        else:
            score = tag_s  # offline / no-embedding fallback: keyword only
        scored.append((score, r))

    scored.sort(key=lambda x: x[0], reverse=True)
    out = []
    for score, r in scored[:k]:
        if score <= 0:
            continue
        out.append({"id": r["id"], "text": r["text"],
                    "source": r["source"], "score": round(float(score), 4)})
    return out


# ── CLI ──────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Build/query the résumé retrieval index")
    ap.add_argument("--build", action="store_true", help="embed changed bullets and cache them")
    ap.add_argument("--force", action="store_true", help="with --build, re-embed everything")
    ap.add_argument("--query", metavar="JD", help="retrieve bullets for this job text")
    ap.add_argument("--title", default="", help="optional job title for the query")
    ap.add_argument("-k", type=int, default=5, help="how many bullets to return")
    args = ap.parse_args()

    if args.build:
        n = build_index(force=args.force)
        print(f"Embedded {n} chunk(s). Index is up to date.")
    if args.query:
        hits = retrieve(args.query, title=args.title, k=args.k)
        if not hits:
            print("No relevant bullets (empty KB or no match).")
        for h in hits:
            print(f"  {h['score']:.3f}  [{h['source']}]\n          {h['text']}")
    if not args.build and not args.query:
        ap.print_help()


if __name__ == "__main__":
    main()
