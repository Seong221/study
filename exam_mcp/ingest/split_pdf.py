"""기출 PDF를 문제 단위 이미지로 분리해 DB에 등록한다.

평가원/학평 수학 문제지는 2단 레이아웃. 각 단(column)에서 "N." 형태의
문항 번호를 찾아, 그 지점부터 같은 단의 다음 번호 직전까지를 잘라낸다.

사용:
  python -m exam_mcp.ingest.split_pdf data/raw/2024-11-g3.pdf \
      --source KICE --year 2024 --month 11 --grade 3 [--track 공통] [--start-page 1]

한계(v1): 한 문제가 단을 넘어 이어지는 경우 뒷부분이 잘릴 수 있다.
분리 결과를 data/problems/에서 눈으로 확인하고, 잘못 잘린 이미지는
직접 수정 후 같은 파일명으로 덮어쓰면 된다.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import pymupdf

from .. import db as dbm
from .answers import provisional_difficulty

NUM_RE = re.compile(r"^(\d{1,2})\s*\.")
POINTS_RE = re.compile(r"\[\s*([234])\s*점\s*\]")  # 문항 본문의 "[3점]" 배점 표기
MAX_PROBLEM_NO = 30
SELECTIVE_START = 23  # 수능 선택과목(확통/미적/기하)은 23~30번이 과목마다 반복된다
HEADER_MARGIN = 130  # 페이지 상단 머리글(교시/홀짝형 표기) 제외
FOOTER_MARGIN = 110  # 하단 쪽번호·저작권 문구 제외
CROP_PAD_TOP = 6
ZOOM = 2.0           # 이미지 해상도 배율


def find_anchors(page: pymupdf.Page) -> list[tuple[int, int, float]]:
    """페이지에서 (문항번호, 단 인덱스, y좌표) 앵커를 찾는다."""
    mid_x = page.rect.width / 2
    anchors = []
    for x0, y0, x1, y1, text, *_ in page.get_text("words"):
        m = NUM_RE.match(text)
        if not m:
            continue
        no = int(m.group(1))
        if not (1 <= no <= MAX_PROBLEM_NO):
            continue
        if y0 < HEADER_MARGIN or y0 > page.rect.height - FOOTER_MARGIN:
            continue
        col = 0 if x0 < mid_x else 1
        # 문항 번호는 단의 왼쪽 가장자리 부근에서 시작한다 (A3 판형은 여백이 넓음)
        col_left = 0 if col == 0 else mid_x
        if x0 - col_left > 130:
            continue
        anchors.append((no, col, y0))
    return anchors


def split_pdf(
    pdf_path: Path,
    *,
    source: str,
    year: int,
    month: int,
    grade: int,
    subject: str = "수학",
    track: str = "",
    start_page: int = 1,
    source_url: str | None = None,
) -> int:
    """PDF를 분리해 DB에 등록하고 시험 id를 반환한다."""
    doc = pymupdf.open(pdf_path)
    conn = dbm.connect()
    exam_id = dbm.get_or_create_exam(
        conn, source=source, year=year, month=month, grade=grade,
        subject=subject, track=track, pdf_path=str(pdf_path), source_url=source_url,
    )
    # 1단계: 문항 앵커를 읽기 순서대로 수집하며 라운드로 나눈다.
    # 수능은 공통(1~22)+첫 선택과목(23~30) 뒤에 다른 선택과목이 23~30번으로 반복되므로,
    # 30번을 채운 뒤 다시 23번이 나오면 새 선택과목 라운드로 취급한다.
    rounds: list[list[tuple[int, int, pymupdf.Rect]]] = [[]]  # [(문항번호, 페이지, 클립영역)]
    expected = 1  # 문항 번호는 읽기 순서상 증가해야 함 (오탐 제거)
    for page_idx in range(start_page - 1, len(doc)):
        page = doc[page_idx]
        mid_x = page.rect.width / 2
        anchors = find_anchors(page)
        # 읽기 순서: 왼쪽 단 위→아래, 오른쪽 단 위→아래
        anchors.sort(key=lambda a: (a[1], a[2]))
        for i, (no, col, y0) in enumerate(anchors):
            if no != expected:
                continue
            # 같은 단의 다음 앵커 직전까지, 없으면 단의 바닥까지
            y_end = page.rect.height - FOOTER_MARGIN
            for no2, col2, y2 in anchors[i + 1:]:
                if col2 == col:
                    y_end = y2 - 4
                    break
            x0 = 0 if col == 0 else mid_x
            x1 = mid_x if col == 0 else page.rect.width
            clip = pymupdf.Rect(x0 + 8, max(y0 - CROP_PAD_TOP, HEADER_MARGIN), x1 - 8, y_end)
            rounds[-1].append((no, page_idx, clip))
            expected += 1
            if expected > MAX_PROBLEM_NO:
                rounds.append([])
                expected = SELECTIVE_START
    if not rounds[-1]:
        rounds.pop()

    def register(eid: int, items: list[tuple[int, int, pymupdf.Rect]], label: str) -> None:
        (dbm.DATA_DIR / "problems" / str(eid)).mkdir(parents=True, exist_ok=True)
        n_pts = pts_sum = 0
        for no, page_idx, clip in items:
            page = doc[page_idx]
            pix = page.get_pixmap(matrix=pymupdf.Matrix(ZOOM, ZOOM), clip=clip)
            img_name = f"{eid}/{no:02d}.png"
            pix.save(dbm.DATA_DIR / "problems" / img_name)
            # 배점은 문항 본문의 "[N점]" 표기에서 추출한다 - 정답표가 파싱 불가여도 채워진다
            m = POINTS_RE.search(page.get_text("text", clip=clip))
            pts = int(m.group(1)) if m else None
            if pts:
                n_pts += 1
                pts_sum += pts
            # 잠정 난이도를 등록 시점에 부여한다 - 정답표 파싱이 실패해도 문제지 생성이
            # 가능해야 하므로. 정답표·실측 정답률 기반 값이 이미 있으면 유지한다.
            conn.execute(
                """INSERT INTO problems (exam_id, number, image_path, difficulty, points)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(exam_id, number) DO UPDATE SET
                       image_path=excluded.image_path,
                       difficulty=COALESCE(problems.difficulty, excluded.difficulty),
                       points=COALESCE(problems.points, excluded.points)""",
                (eid, no, img_name, provisional_difficulty(no), pts),
            )
        conn.commit()
        print(f"[완료] 시험 id={eid}{label}: 문항 {len(items)}개 분리")
        print(f"[배점] 문제지 본문에서 {n_pts}/{len(items)}문항 배점 추출 (합계 {pts_sum}점)")
        if n_pts < len(items) or (len(items) >= 20 and pts_sum != 100):
            print("[주의] 배점 추출이 불완전합니다. 정답표 파싱이나 set_answers_text로 보완하세요.")
        if len(items) < 8:
            print("[주의] 분리된 문항이 적습니다. PDF 레이아웃이 다르거나 스캔본일 수 있으니 이미지를 확인하세요.")

    def detect_track(page_idx: int, fallback: str) -> str:
        head = doc[page_idx].get_text().replace(" ", "")
        for kw in ("미적분", "기하", "확률과통계"):
            if kw in head:
                return kw
        return fallback

    register(exam_id, rounds[0], "")

    # 2단계: 추가 선택과목(미적분·기하 등)은 track을 붙여 별도 시험으로 등록한다.
    # 문제지 PDF에 홀수형+짝수형이 함께 든 경우 짝수형 구간에서 같은 과목이 반복되므로,
    # 본시험의 첫 선택과목을 포함해 이미 본 track이 다시 나오면 거기서 중단한다(중복 방지).
    first_sel = next((it for it in rounds[0] if it[0] == SELECTIVE_START), None)
    seen_tracks = {detect_track(first_sel[1], "확률과통계")} if first_sel else set()
    order_fallback = ["미적분", "기하"]
    for r_idx, rnd in enumerate(rounds[1:]):
        fb = order_fallback[r_idx] if r_idx < len(order_fallback) else f"선택{r_idx + 2}"
        tr = detect_track(rnd[0][1], fb)
        if tr in seen_tracks:
            break  # 짝수형(중복 문항) 구간 진입 - 이후 라운드는 전부 중복
        seen_tracks.add(tr)
        eid2 = dbm.get_or_create_exam(
            conn, source=source, year=year, month=month, grade=grade,
            subject=subject, track=tr, pdf_path=str(pdf_path), source_url=source_url,
        )
        register(eid2, rnd, f" ({tr})")

    return exam_id


def main() -> None:
    ap = argparse.ArgumentParser(description="기출 PDF를 문제 이미지로 분리")
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--source", required=True, choices=["KICE", "OFFICE"])
    ap.add_argument("--year", required=True, type=int)
    ap.add_argument("--month", required=True, type=int)
    ap.add_argument("--grade", required=True, type=int, choices=[1, 2, 3])
    ap.add_argument("--subject", default="수학")
    ap.add_argument("--track", default="")
    ap.add_argument("--start-page", type=int, default=1, help="문제 시작 페이지(표지 건너뛰기)")
    args = ap.parse_args()
    split_pdf(
        args.pdf, source=args.source, year=args.year, month=args.month,
        grade=args.grade, subject=args.subject, track=args.track,
        start_page=args.start_page,
    )


if __name__ == "__main__":
    main()
