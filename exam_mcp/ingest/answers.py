"""정답표 PDF 자동 파싱 → 정답·배점 입력 + 잠정 난이도 부여.

정답표는 (문항번호, 정답, 배점) 셀이 반복되는 표 구조다.
텍스트 토큰을 훑으며 [1~30 정수][①~⑤ 또는 0~999][배점 2/3/4] 패턴을 수집하고,
같은 번호가 여러 번 나오면(수능 선택과목 반복) 첫 번째 것만 쓴다
- split_pdf가 첫 번째 선택과목만 수집하는 것과 일치.

검증: 문항 번호가 빠짐없이 이어지고 배점 합계가 100점이어야 DB에 쓴다.
"""
from __future__ import annotations

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
            "view_answer_sheet로 정답표를 읽고 set_answers로 입력하세요."
        )
    return apply_triplets(exam_id, triplets)
