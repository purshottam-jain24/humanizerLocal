"""Local reference corpus: import PDF / DOCX / TXT files and folders, index them.

Everything is stored locally in SQLite. Imported files are copied into the
data directory so the index stays valid if the originals move.
"""

from __future__ import annotations

import io
import re
import shutil
from pathlib import Path

import numpy as np

from app.core.config import config
from app.services.plagiarism.fingerprint_service import fingerprints, tokens
from app.storage import db

SUPPORTED = {".pdf", ".docx", ".txt", ".md"}
CHUNK_WORDS = 180


class CorpusImportError(Exception):
    pass


def extract_text(path: Path) -> tuple[str, dict]:
    ext = path.suffix.lower()
    meta: dict = {}
    if ext in (".txt", ".md"):
        raw = path.read_bytes()
        for enc in ("utf-8", "utf-16", "latin-1"):
            try:
                return raw.decode(enc), meta
            except UnicodeDecodeError:
                continue
    if ext == ".pdf":
        from pypdf import PdfReader

        try:
            reader = PdfReader(str(path))
            if reader.is_encrypted:
                try:
                    reader.decrypt("")
                except Exception as e:  # noqa: BLE001
                    raise CorpusImportError("Encrypted PDF") from e
            pages = [(pg.extract_text() or "") for pg in reader.pages]
        except CorpusImportError:
            raise
        except Exception as e:  # noqa: BLE001
            raise CorpusImportError(f"Could not read PDF: {e}") from e
        meta["pages"] = len(pages)
        if reader.metadata and reader.metadata.title:
            meta["title"] = str(reader.metadata.title)
        text = "\n\n".join(pages)
        if len(text.strip()) < 20:
            raise CorpusImportError("The PDF has no extractable text (it may be scanned images).")
        return text, meta
    if ext == ".docx":
        from app.services.document.validation import validate_docx_bytes
        from app.services.extraction.docx_parser import extract_document
        from app.services.ooxml.package import DocxPackage

        data = path.read_bytes()
        rep = validate_docx_bytes(data, path.name, 200 * 1024 * 1024)
        st = extract_document(DocxPackage(data, rep.main_part)).structure
        if st["metadata"].get("title"):
            meta["title"] = st["metadata"]["title"]
        return "\n\n".join(p["text"].replace("￼", "") for p in st["paragraphs"] if p["part"] in ("body", "footnotes")), meta
    raise CorpusImportError(f"Unsupported file type: {ext}")


def chunk_text(text: str, target: int = CHUNK_WORDS) -> list[str]:
    # De-hyphenate PDF line breaks and normalise whitespace within paragraphs.
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    paras = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n", text)]
    paras = [p for p in paras if p]
    chunks: list[str] = []
    cur: list[str] = []
    cur_words = 0
    for p in paras:
        words = p.split()
        if len(words) > target * 1.5:
            # split long paragraphs on sentence boundaries
            sents = re.split(r"(?<=[.!?])\s+", p)
            for s in sents:
                sw = len(s.split())
                if cur_words + sw > target and cur:
                    chunks.append(" ".join(cur))
                    cur, cur_words = [], 0
                cur.append(s)
                cur_words += sw
            continue
        if cur_words + len(words) > target and cur:
            chunks.append(" ".join(cur))
            cur, cur_words = [], 0
        cur.append(p)
        cur_words += len(words)
    if cur:
        chunks.append(" ".join(cur))
    return [c for c in chunks if len(c.split()) >= 5]


def add_source(title: str, text: str, *, kind: str, origin: str = "local", path: str | None = None,
               url: str | None = None, meta: dict | None = None) -> dict:
    chunks = chunk_text(text)
    if not chunks:
        raise CorpusImportError("No usable text found.")
    sid = db.new_id("src")
    words = 0
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO corpus_sources(id,title,path,kind,origin,url,meta_json,chunk_count,word_count,added_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (sid, title, path, kind, origin, url, db.dumps(meta or {}), len(chunks), 0, db.now()),
        )
        for i, ch in enumerate(chunks):
            cid = f"{sid}:{i}"
            toks = tokens(ch)
            words += len(toks)
            conn.execute("INSERT INTO corpus_chunks(id,source_id,ord,text,token_count) VALUES (?,?,?,?,?)",
                         (cid, sid, i, ch, len(toks)))
            conn.executemany("INSERT INTO fingerprints(hash,chunk_id,pos) VALUES (?,?,?)",
                             [(h, cid, pos) for h, pos in fingerprints(ch)])
        conn.execute("UPDATE corpus_sources SET word_count=? WHERE id=?", (words, sid))
    invalidate_cache()
    return get_source(sid)


def import_file(path: Path, display_name: str | None = None) -> dict:
    ext = path.suffix.lower()
    if ext not in SUPPORTED:
        raise CorpusImportError(f"Unsupported file type: {ext}")
    text, meta = extract_text(path)
    dest_dir = config.corpus_dir
    dest_dir.mkdir(parents=True, exist_ok=True)
    stored = dest_dir / f"{db.new_id('f')}{ext}"
    shutil.copyfile(path, stored)
    title = meta.get("title") or display_name or path.name
    return add_source(title, text, kind=ext.lstrip("."), origin="local", path=str(stored),
                      meta={**meta, "original_name": display_name or path.name})


def import_bytes(name: str, data: bytes) -> dict:
    ext = Path(name).suffix.lower()
    if ext not in SUPPORTED:
        raise CorpusImportError(f"Unsupported file type: {ext}")
    tmp = config.corpus_dir / f"upload-{db.new_id('t')}{ext}"
    config.corpus_dir.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(data)
    try:
        return import_file(tmp, display_name=name)
    finally:
        tmp.unlink(missing_ok=True)


def import_folder(folder: Path, recursive: bool = True) -> dict:
    if not folder.is_dir():
        raise CorpusImportError("Folder not found.")
    imported, failed = [], []
    it = folder.rglob("*") if recursive else folder.glob("*")
    for f in sorted(it):
        if f.is_file() and f.suffix.lower() in SUPPORTED and not f.name.startswith(("~$", ".")):
            try:
                imported.append(import_file(f))
            except Exception as e:  # noqa: BLE001
                failed.append({"file": str(f), "error": str(e)[:200]})
    return {"imported": imported, "failed": failed}


def list_sources() -> list[dict]:
    rows = db.query("SELECT * FROM corpus_sources ORDER BY added_at DESC")
    for r in rows:
        r["meta"] = db.loads(r.pop("meta_json"), {})
        r["embedded"] = bool(db.query_one("SELECT 1 FROM corpus_chunks WHERE source_id=? AND embedding IS NOT NULL LIMIT 1", (r["id"],)))
    return rows


def get_source(sid: str) -> dict:
    r = db.query_one("SELECT * FROM corpus_sources WHERE id=?", (sid,))
    if r:
        r["meta"] = db.loads(r.pop("meta_json"), {})
    return r


def delete_source(sid: str) -> None:
    src = get_source(sid)
    with db.transaction() as conn:
        conn.execute("DELETE FROM fingerprints WHERE chunk_id IN (SELECT id FROM corpus_chunks WHERE source_id=?)", (sid,))
        conn.execute("DELETE FROM corpus_chunks WHERE source_id=?", (sid,))
        conn.execute("DELETE FROM corpus_sources WHERE id=?", (sid,))
    if src and src.get("path"):
        p = Path(src["path"])
        if p.is_file() and config.corpus_dir in p.parents:
            p.unlink(missing_ok=True)
    invalidate_cache()


async def embed_corpus(model: str, ollama, on_progress=None) -> int:
    rows = db.query("SELECT c.id, c.text FROM corpus_chunks c JOIN corpus_sources s ON s.id=c.source_id "
                    "WHERE c.embedding IS NULL OR s.embed_model IS NOT ? ", (model,))
    done = 0
    for i in range(0, len(rows), 16):
        batch = rows[i:i + 16]
        vecs = await ollama.embed(model, [r["text"] for r in batch])
        with db.transaction() as conn:
            for r, v in zip(batch, vecs):
                arr = np.asarray(v, dtype=np.float32)
                arr /= (np.linalg.norm(arr) or 1.0)
                conn.execute("UPDATE corpus_chunks SET embedding=? WHERE id=?", (arr.tobytes(), r["id"]))
        done += len(batch)
        if on_progress:
            on_progress(done, len(rows))
    db.execute("UPDATE corpus_sources SET embed_model=?", (model,))
    invalidate_cache()
    return done


_cache: dict = {}


def invalidate_cache() -> None:
    _cache.clear()


def corpus_stats() -> dict:
    r = db.query_one("SELECT COUNT(*) AS sources, COALESCE(SUM(chunk_count),0) AS chunks, COALESCE(SUM(word_count),0) AS words FROM corpus_sources")
    emb = db.query_one("SELECT COUNT(*) AS n FROM corpus_chunks WHERE embedding IS NOT NULL")
    models = db.query("SELECT DISTINCT embed_model FROM corpus_sources WHERE embed_model IS NOT NULL")
    return {**r, "embedded_chunks": emb["n"], "embed_models": [m["embed_model"] for m in models]}


def load_chunk_matrix(model: str | None) -> tuple[list[str], np.ndarray | None]:
    key = ("emb", model)
    if key in _cache:
        return _cache[key]
    rows = db.query("SELECT c.id, c.embedding FROM corpus_chunks c JOIN corpus_sources s ON s.id=c.source_id "
                    "WHERE c.embedding IS NOT NULL AND s.embed_model IS ?", (model,))
    if not rows:
        _cache[key] = ([], None)
        return _cache[key]
    ids = [r["id"] for r in rows]
    mat = np.vstack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
    _cache[key] = (ids, mat)
    return _cache[key]


def load_tfidf_index() -> dict:
    if "tfidf" in _cache:
        return _cache["tfidf"]
    import math
    from collections import Counter, defaultdict

    rows = db.query("SELECT id, text FROM corpus_chunks")
    df: Counter = Counter()
    tfs: dict[str, Counter] = {}
    for r in rows:
        tf = Counter(t for t in tokens(r["text"]) if t not in STOPWORDS and len(t) > 2)
        tfs[r["id"]] = tf
        df.update(tf.keys())
    n = max(1, len(rows))
    idf = {t: math.log((1 + n) / (1 + c)) + 1 for t, c in df.items()}
    inverted: dict[str, list[tuple[str, float]]] = defaultdict(list)
    norms: dict[str, float] = {}
    for cid, tf in tfs.items():
        vec = {t: (1 + math.log(c)) * idf[t] for t, c in tf.items()}
        norms[cid] = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        for t, v in vec.items():
            inverted[t].append((cid, v))
    _cache["tfidf"] = {"idf": idf, "inverted": inverted, "norms": norms, "n": n}
    return _cache["tfidf"]


STOPWORDS = set("""a about above after again against all am an and any are as at be because been before being below between both but by
can could did do does doing down during each few for from further had has have having he her here hers herself him himself his how i if in
into is it its itself just me more most my myself no nor not now of off on once only or other our ours ourselves out over own same she
should so some such than that the their theirs them themselves then there these they this those through to too under until up very was
we were what when where which while who whom why will with would you your yours yourself yourselves also however may might must shall
""".split())
