"""평가원 스타일 문제지 조립.

- 난이도 배분: 평가원 수학 체감 곡선(앞은 쉽게, 뒤로 갈수록 어렵게, 마지막 구간에 최고난도)
- 출제율: 같은 학년/과목 시험 전체에서 해당 유형(topic)이 출제된 시험 비율
"""
from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass, field

# 평가원 수학 기준 난이도 구성비 (하/중/상)
DEFAULT_MIX = {"하": 0.35, "중": 0.45, "상": 0.20}

# 문제지 하나는 반드시 이 개수 이상의 서로 다른 시험에서 구성한다
MIN_SOURCE_EXAMS = 3


@dataclass
class SelectedProblem:
    position: int              # 문제지 내 번호 (1부터)
    problem_id: int
    exam_title: str
    original_number: int
    points: int | None
    answer: str | None
    answer_rate: float | None
    difficulty: str
    unit: str | None
    topic: str | None
    frequency: float | None    # 출제율(%)
    image_path: str | None


@dataclass
class BuiltExam:
    problems: list[SelectedProblem]
    ramp_points: dict[str, int] = field(default_factory=dict)  # {"중": 8, "상": 17} = 해당 번호부터 난이도 상승
    notes: list[str] = field(default_factory=list)


def difficulty_sequence(count: int, mix: dict[str, float] | None = None) -> list[str]:
    """문항 수에 맞는 난이도 배열을 만든다. 평가원처럼 오름차순 배치."""
    mix = mix or DEFAULT_MIX
    n_low = round(count * mix["하"])
    n_high = max(1, round(count * mix["상"])) if count >= 5 else round(count * mix["상"])
    n_mid = count - n_low - n_high
    return ["하"] * n_low + ["중"] * n_mid + ["상"] * n_high


def topic_frequency(conn: sqlite3.Connection, grade: int, subject: str, topic: str | None) -> float | None:
    """유형(topic)의 출제율(%): 해당 학년/과목 시험 중 이 유형이 나온 시험의 비율."""
    if not topic:
        return None
    total = conn.execute(
        "SELECT COUNT(DISTINCT e.id) FROM exams e WHERE e.grade=? AND e.subject=?",
        (grade, subject),
    ).fetchone()[0]
    if not total:
        return None
    hit = conn.execute(
        """SELECT COUNT(DISTINCT e.id) FROM exams e
           JOIN problems p ON p.exam_id = e.id
           WHERE e.grade=? AND e.subject=? AND p.topic=?""",
        (grade, subject, topic),
    ).fetchone()[0]
    return round(hit / total * 100, 1)


def build_exam(
    conn: sqlite3.Connection,
    *,
    grade: int,
    subject: str = "수학",
    count: int = 20,
    units: list[str] | None = None,
    source: str | None = None,      # 'KICE' | 'OFFICE' | None(전체)
    mix: dict[str, float] | None = None,
    seed: int | None = None,
) -> BuiltExam:
    rng = random.Random(seed)
    seq = difficulty_sequence(count, mix)
    notes: list[str] = []

    # 난이도별 후보 문제 조회
    base_sql = """
        SELECT p.*, e.title AS exam_title,
               e.source AS exam_source, e.year AS exam_year, e.month AS exam_month
        FROM problems p
        JOIN exams e ON e.id = p.exam_id
        WHERE e.grade=? AND e.subject=? AND p.difficulty=?
    """
    params_tail: list = []
    if units:
        base_sql += f" AND p.unit IN ({','.join('?' * len(units))})"
        params_tail += units
    if source:
        base_sql += " AND e.source=?"
        params_tail.append(source)

    pools: dict[str, list[sqlite3.Row]] = {}
    for level in ("하", "중", "상"):
        rows = conn.execute(base_sql, (grade, subject, level, *params_tail)).fetchall()
        rng.shuffle(rows)
        pools[level] = rows

    # 최소 출처 검증은 '시행'(출제기관·연도·월) 기준 - 같은 수능의 선택과목(track)들이
    # 별도 시험으로 등록되어도 하나의 시행으로 센다.
    def sitting(row: sqlite3.Row) -> tuple:
        return (row["exam_source"], row["exam_year"], row["exam_month"])

    available = {sitting(row) for rows in pools.values() for row in rows}
    if len(available) < MIN_SOURCE_EXAMS:
        return BuiltExam(problems=[], notes=[
            f"문제지는 서로 다른 시행(시험 회차) {MIN_SOURCE_EXAMS}개 이상에서 구성해야 하는데, "
            f"조건에 맞는 회차가 {len(available)}개뿐입니다. "
            "시험을 더 등록하거나 필터(units/source)를 넓히세요."
        ])

    # 부족한 난이도는 인접 난이도에서 보충
    fallback = {"하": ["중", "상"], "중": ["하", "상"], "상": ["중", "하"]}
    picked: list[tuple[str, sqlite3.Row]] = []
    used_ids: set[int] = set()
    used_sittings: set[tuple] = set()

    def take(lv: str) -> sqlite3.Row | None:
        pool = pools[lv]
        # 시행이 3개 미만인 동안은 아직 안 쓴 회차의 문제를 우선 선택
        if len(used_sittings) < MIN_SOURCE_EXAMS:
            for idx in range(len(pool) - 1, -1, -1):
                if sitting(pool[idx]) not in used_sittings and pool[idx]["id"] not in used_ids:
                    return pool.pop(idx)
        while pool:
            cand = pool.pop()
            if cand["id"] not in used_ids:
                return cand
        return None

    for level in seq:
        row = None
        for lv in [level, *fallback[level]]:
            row = take(lv)
            if row is not None:
                if lv != level:
                    notes.append(f"'{level}' 난이도 문제가 부족해 '{lv}' 문제로 대체된 문항이 있습니다.")
                break
        if row is None:
            notes.append(f"조건에 맞는 문제가 부족해 {count}문항 중 {len(picked)}문항만 구성했습니다.")
            break
        used_ids.add(row["id"])
        used_sittings.add(sitting(row))
        picked.append((level, row))

    if picked and len(used_sittings) < MIN_SOURCE_EXAMS:
        return BuiltExam(problems=[], notes=[
            f"선별 결과 출처 회차가 {len(used_sittings)}개뿐이라 문제지를 구성할 수 없습니다 "
            f"(최소 {MIN_SOURCE_EXAMS}개). 문항 수를 늘리거나 시험을 더 등록하세요."
        ])

    # 난이도 상승 지점 계산 (실제 배치된 난이도 기준)
    ramp: dict[str, int] = {}
    for i, (_, row) in enumerate(picked, start=1):
        d = row["difficulty"]
        if d in ("중", "상") and d not in ramp:
            ramp[d] = i

    problems = [
        SelectedProblem(
            position=i,
            problem_id=row["id"],
            exam_title=row["exam_title"],
            original_number=row["number"],
            points=row["points"],
            answer=row["answer"],
            answer_rate=row["answer_rate"],
            difficulty=row["difficulty"],
            unit=row["unit"],
            topic=row["topic"],
            frequency=topic_frequency(conn, grade, subject, row["topic"]),
            image_path=row["image_path"],
        )
        for i, (_, row) in enumerate(picked, start=1)
    ]
    return BuiltExam(problems=problems, ramp_points=ramp, notes=sorted(set(notes)))
