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
        SELECT p.*, e.title AS exam_title FROM problems p
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

    # 부족한 난이도는 인접 난이도에서 보충
    fallback = {"하": ["중", "상"], "중": ["하", "상"], "상": ["중", "하"]}
    picked: list[tuple[str, sqlite3.Row]] = []
    used_ids: set[int] = set()
    for level in seq:
        row = None
        for lv in [level, *fallback[level]]:
            while pools[lv]:
                cand = pools[lv].pop()
                if cand["id"] not in used_ids:
                    row = cand
                    if lv != level:
                        notes.append(f"'{level}' 난이도 문제가 부족해 '{lv}' 문제로 대체된 문항이 있습니다.")
                    break
            if row:
                break
        if row is None:
            notes.append(f"조건에 맞는 문제가 부족해 {count}문항 중 {len(picked)}문항만 구성했습니다.")
            break
        used_ids.add(row["id"])
        picked.append((level, row))

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
