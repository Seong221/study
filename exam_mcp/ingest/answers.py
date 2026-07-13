"""정답표 PDF 자동 파싱 → 정답·배점 입력 + 잠정 난이도 부여.

정답표는 (문항번호, 정답, 배점) 셀이 반복되는 표 구조다.
텍스트 토큰을 훑으며 [1~30 정수][①~⑤ 또는 0~999][배점 2/3/4] 패턴을 수집하고,
같은 번호가 여러 번 나오면(수능 선택과목 반복) 첫 번째 것만 쓴다
- split_pdf가 첫 번째 선택과목만 수집하는 것과 일치.

검증: 문항 번호가 빠짐없이 이어지고 배점 합계가 100점이어야 DB에 쓴다.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pymupdf

from .. import db as dbm

CIRCLED = {"①": "1", "②": "2", "③": "3", "④": "4", "⑤": "5"}

# 평가원 수학 문항 배치 기반 잠정 난이도 (실측 정답률 입력 시 자동 대체됨)
PROVISIONAL_DIFFICULTY = {
    "하": {1, 2, 3, 4, 5, 6, 7, 8, 16, 17, 23, 24},
    "중": {9, 10, 11, 12, 13, 18, 19, 25, 26, 27},
    "상": {14, 15, 20, 21, 22, 28, 29, 30},
}


def provisional_difficulty(number: int) -> str:
    for diff, nums in PROVISIONAL_DIFFICULTY.items():
        if number in nums:
            return diff
    return "중"


def _as_int(s: str) -> int | None:
    try:
        return int(s)
    except ValueError:
        return None


def parse_answer_pdf(pdf_path: Path, page_index: int = 0) -> dict[int, tuple[str, int]]:
    """정답표에서 {문항번호: (정답, 배점)}을 추출한다. (홀수형 = 첫 페이지)"""
    doc = pymupdf.open(pdf_path)
    try:
        tokens = doc[page_index].get_text().split()
    finally:
        doc.close()

    result: dict[int, tuple[str, int]] = {}
    i = 0
    while i < len(tokens) - 2:
        num = _as_int(tokens[i])
        if num is not None and 1 <= num <= 30:
            ans = CIRCLED.get(tokens[i + 1])
            if ans is None:
                a = _as_int(tokens[i + 1])
                ans = str(a) if a is not None and 0 <= a <= 999 else None
            pts = _as_int(tokens[i + 2])
            if ans is not None and pts in (2, 3, 4):
                result.setdefault(num, (ans, pts))  # 첫 번째 선택과목만
                i += 3
                continue
        i += 1
    return result


def validate(triplets: dict[int, tuple[str, int]]) -> list[str]:
    """빠진 번호·배점 합계를 검사해 문제 목록을 반환 (비어 있으면 통과)."""
    issues = []
    if not triplets:
        return ["정답을 하나도 찾지 못했습니다."]
    nums = sorted(triplets)
    expected = list(range(1, max(nums) + 1))
    missing = [n for n in expected if n not in triplets]
    if missing:
        issues.append(f"빠진 문항: {missing}")
    total = sum(p for _, p in triplets.values())
    if total != 100:
        issues.append(f"배점 합계가 {total}점입니다 (100점이어야 함).")
    return issues


def apply_triplets(exam_id: int, triplets: dict[int, tuple[str, int]]) -> str:
    """검증된 {번호: (정답, 배점)}을 DB에 기록한다. 실측 정답률이 있는 문항의 난이도는 건드리지 않는다."""
    issues = validate(triplets)
    if issues:
        return "[정답 입력 실패] " + " / ".join(issues) + " - DB에 쓰지 않았습니다."

    conn = dbm.connect()
    for num, (ans, pts) in triplets.items():
        conn.execute(
            """UPDATE problems SET answer=?, points=?,
                   difficulty = CASE WHEN answer_rate IS NULL THEN ? ELSE difficulty END
               WHERE exam_id=? AND number=?""",
            (ans, pts, provisional_difficulty(num), exam_id, num),
        )
    conn.commit()
    return f"[정답 입력] {len(triplets)}문항 (배점 합계 100점 검증 통과, 난이도는 배치 기반 잠정치)"


def apply_answers(exam_id: int, pdf_path: Path) -> str:
    """정답표를 파싱·검증해 해당 시험 문항에 정답·배점·잠정 난이도를 기록한다."""
    triplets = parse_answer_pdf(pdf_path)
    if not triplets:
        return (
            "[정답 자동 입력 실패] 정답표에 텍스트 레이어가 없습니다(벡터/스캔 PDF). "
            "사용자에게 정답 목록(예: 1③ 2⑤ 3④ …)을 붙여넣어 달라고 요청해 "
            "set_answers_text로 입력하세요."
        )
    return apply_triplets(exam_id, triplets)


def _walk_triplets(tokens: list[str]) -> dict[int, tuple[str, int]]:
    """토큰 열에서 [번호][정답][배점 2/3/4] 패턴을 수집한다 (정답표 표 구조)."""
    result: dict[int, tuple[str, int]] = {}
    i = 0
    while i < len(tokens) - 2:
        num = _as_int(tokens[i])
        if num is not None and 1 <= num <= 30:
            a = _as_int(tokens[i + 1])
            ans = str(a) if a is not None and 0 <= a <= 999 else None
            pts = _as_int(tokens[i + 2])
            if ans is not None and pts in (2, 3, 4):
                result.setdefault(num, (ans, pts))
                i += 3
                continue
        i += 1
    return result


def _walk_pairs(tokens: list[str]) -> dict[int, str]:
    """토큰 열에서 (번호, 정답) 교대 패턴을 수집한다. 번호는 1부터 오름차순이어야 한다."""
    result: dict[int, str] = {}
    expected = 1
    i = 0
    while i < len(tokens) - 1:
        if tokens[i] == str(expected):
            a = _as_int(tokens[i + 1])
            if a is not None and 0 <= a <= 999:
                result[expected] = str(a)
                expected += 1
                i += 2
                continue
        i += 1
    return result


def apply_answers_text(exam_id: int, text: str) -> str:
    """자유 형식 정답 텍스트를 해석해 기록한다 - 형식 파악은 전부 서버가 한다.

    "1③ 2⑤ …", "1번 3, 2번 5", 표 복사본(번호 정답 배점) 모두 허용.
    배점이 포함된 3열 형식이면 배점까지, 아니면 정답만 기록한다
    (배점은 문제지 본문에서 추출된 기존 값 유지).
    """
    # 원문자 정답(①~⑤)을 숫자로 바꾸고 숫자 토큰만 추출 - "1③"처럼 붙어 있어도 분리된다
    tokens = [CIRCLED.get(t, t) for t in re.findall(r"[①②③④⑤]|\d+", text)]

    # 1) 배점 포함 3열 형식 시도 (완전 검증 통과 시에만 채택)
    triplets = _walk_triplets(tokens)
    if triplets and not validate(triplets):
        return apply_triplets(exam_id, triplets)

    # 2) (번호, 정답) 쌍 형식 - 정답만 기록
    pairs = _walk_pairs(tokens)
    conn = dbm.connect()
    nums = [r["number"] for r in conn.execute(
        "SELECT number FROM problems WHERE exam_id=? ORDER BY number", (exam_id,))]
    if not nums:
        return f"[정답 입력 실패] 시험 id={exam_id}에 등록된 문항이 없습니다."
    missing = [n for n in nums if n not in pairs]
    if missing:
        return (
            f"[정답 입력 실패] 해석 결과 빠진 문항이 있습니다: {missing}. "
            f"해석된 정답: {json.dumps(pairs, ensure_ascii=False)}. "
            "'1③ 2⑤ …'처럼 번호-정답 순서로 다시 붙여넣어 주세요. DB에 쓰지 않았습니다."
        )
    for num in nums:
        conn.execute(
            "UPDATE problems SET answer=? WHERE exam_id=? AND number=?",
            (pairs[num], exam_id, num),
        )
    conn.commit()
    echo = " ".join(f"{n}:{pairs[n]}" for n in nums)
    return (
        f"[정답 입력] {len(nums)}문항 (정답만 기록, 배점·난이도는 기존 값 유지)\n"
        f"해석 결과를 사용자에게 확인받으세요 → {echo}"
    )
