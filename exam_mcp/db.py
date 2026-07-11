"""SQLite 문제은행 스키마 및 연결 관리."""
from __future__ import annotations

import sqlite3
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "exam.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS exams (
    id        INTEGER PRIMARY KEY,
    source    TEXT NOT NULL,            -- 'KICE'(평가원) | 'OFFICE'(교육청 학평)
    year      INTEGER NOT NULL,         -- 시행 연도
    month     INTEGER NOT NULL,         -- 시행 월 (3, 6, 9, 10, 11 등)
    grade     INTEGER NOT NULL,         -- 1, 2, 3
    subject   TEXT NOT NULL,            -- '수학'
    track     TEXT NOT NULL DEFAULT '', -- 공통/확률과통계/미적분/기하 등
    title      TEXT,                    -- 표시용 이름 (예: 2024학년도 수능 수학)
    pdf_path   TEXT,
    source_url TEXT,                    -- 원자료를 내려받은 공식 사이트 URL (출처 표기용)
    UNIQUE(source, year, month, grade, subject, track)
);

CREATE TABLE IF NOT EXISTS problems (
    id          INTEGER PRIMARY KEY,
    exam_id     INTEGER NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
    number      INTEGER NOT NULL,       -- 문항 번호
    points      INTEGER,                -- 배점 (2/3/4)
    answer      TEXT,                   -- 정답
    answer_rate REAL,                   -- 정답률(%) - EBSi 등 공개 자료
    difficulty  TEXT,                   -- '상'/'중'/'하'
    unit        TEXT,                   -- 대단원 (예: 이차함수, 지수와 로그)
    topic       TEXT,                   -- 세부 유형 (예: 이차함수 최대최소)
    image_path  TEXT,                   -- 문제 이미지 (data/problems/ 기준 상대경로)
    latex_text  TEXT,                   -- 선택: 텍스트/LaTeX 변환본
    UNIQUE(exam_id, number)
);

CREATE INDEX IF NOT EXISTS idx_problems_lookup
    ON problems(exam_id, difficulty, unit);
"""


# 출처 표기용: 도메인 → 기관명
SOURCE_SITES = {
    "suneung.re.kr": "한국교육과정평가원 수능 홈페이지",
    "kice.re.kr": "한국교육과정평가원",
    "sen.go.kr": "서울특별시교육청",
    "ice.go.kr": "인천광역시교육청",
    "goe.go.kr": "경기도교육청",
    "pen.go.kr": "부산광역시교육청",
    "ebsi.co.kr": "EBSi",
    "ebs.co.kr": "EBS",
}


def site_name(url: str | None) -> str | None:
    """URL의 도메인을 기관명으로 변환한다."""
    if not url:
        return None
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower()
    for domain, name in SOURCE_SITES.items():
        if host == domain or host.endswith("." + domain):
            return name
    return host or None


def connect(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    """DB에 연결하고 스키마가 없으면 생성한다."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")  # 원격 다중 접속 대비
    conn.executescript(SCHEMA)
    # 기존 DB 마이그레이션: source_url 컬럼이 없으면 추가
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(exams)")}
    if "source_url" not in cols:
        conn.execute("ALTER TABLE exams ADD COLUMN source_url TEXT")
        conn.commit()
    return conn


def difficulty_from_rate(answer_rate: float) -> str:
    """정답률(%) → 상/중/하 매핑. 평가원 체감 난이도 기준의 통상적 구간."""
    if answer_rate >= 80:
        return "하"
    if answer_rate >= 40:
        return "중"
    return "상"


def get_or_create_exam(
    conn: sqlite3.Connection,
    *,
    source: str,
    year: int,
    month: int,
    grade: int,
    subject: str = "수학",
    track: str = "",
    title: str | None = None,
    pdf_path: str | None = None,
    source_url: str | None = None,
) -> int:
    row = conn.execute(
        "SELECT id FROM exams WHERE source=? AND year=? AND month=? AND grade=? AND subject=? AND track=?",
        (source, year, month, grade, subject, track),
    ).fetchone()
    if row:
        if source_url:
            conn.execute("UPDATE exams SET source_url=? WHERE id=? AND source_url IS NULL",
                         (source_url, row["id"]))
            conn.commit()
        return row["id"]
    if title is None:
        src_name = "평가원" if source == "KICE" else "교육청 학평"
        title = f"{year}년 {month}월 {src_name} 고{grade} {subject}" + (f" ({track})" if track else "")
    cur = conn.execute(
        "INSERT INTO exams (source, year, month, grade, subject, track, title, pdf_path, source_url) VALUES (?,?,?,?,?,?,?,?,?)",
        (source, year, month, grade, subject, track, title, pdf_path, source_url),
    )
    conn.commit()
    return cur.lastrowid
