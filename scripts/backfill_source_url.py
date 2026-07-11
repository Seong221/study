"""기존 시험들의 source_url을 manifest에서 소급 적용한다 (1회성)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from exam_mcp import db as dbm
from exam_mcp.ingest.fetch import RAW_DIR, manifest_url_for

conn = dbm.connect()
for e in conn.execute("SELECT id, pdf_path FROM exams").fetchall():
    rel = Path(e["pdf_path"]).resolve().relative_to(RAW_DIR.resolve()).as_posix()
    url = manifest_url_for(rel)
    conn.execute("UPDATE exams SET source_url=? WHERE id=?", (url, e["id"]))
    print(e["id"], "->", (url[:80] if url else None))
conn.commit()
