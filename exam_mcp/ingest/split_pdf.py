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

NUM_RE = re.compile(r"^(\d{1,2})\s*\.")
MAX_PROBLEM_NO = 30
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
    out_dir = dbm.DATA_DIR / "problems" / str(exam_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    expected = 1  # 문항 번호는 읽기 순서상 증가해야 함 (오탐 제거)
    saved = 0
    for page_idx in range(start_page - 1, len(doc)):
        page = doc[page_idx]
        mid_x = page.rect.width / 2
        anchors = find_anchors(page)
        # 읽기 순서: 왼쪽 단 위→아래, 오른쪽 단 위→아래
        anchors.sort(key=lambda a: (a[1], a[2]))
        anchors = [a for a in anchors if a[0] >= expected]

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
            pix = page.get_pixmap(matrix=pymupdf.Matrix(ZOOM, ZOOM), clip=clip)
            img_name = f"{exam_id}/{no:02d}.png"
            pix.save(dbm.DATA_DIR / "problems" / img_name)
            conn.execute(
                """INSERT INTO problems (exam_id, number, image_path) VALUES (?,?,?)
                   ON CONFLICT(exam_id, number) DO UPDATE SET image_path=excluded.image_path""",
                (exam_id, no, img_name),
            )
            saved += 1
            expected = no + 1
    conn.commit()
    print(f"[완료] 시험 id={exam_id}: 문항 {saved}개 분리 → {out_dir}")
    if saved < 20:
        print("[주의] 분리된 문항이 적습니다. PDF 레이아웃이 다르거나 스캔본일 수 있으니 이미지를 확인하세요.")
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
