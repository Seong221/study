"""가짜 데이터로 문제은행 → 문제지 생성 전체 흐름을 검증한다.
사용: .venv/Scripts/python scripts/smoke_test.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from exam_mcp import db as dbm
from exam_mcp.exam_builder import build_exam
from exam_mcp.render import render_html

tmp = Path(tempfile.mkdtemp()) / "test.db"
conn = dbm.connect(tmp)

# 가짜 시험 3개 × 30문항 생성
units = ["다항식", "이차함수", "도형의 방정식", "집합과 명제", "함수와 그래프"]
for month in (3, 6, 9):
    exam_id = dbm.get_or_create_exam(conn, source="OFFICE", year=2025, month=month, grade=1)
    for no in range(1, 31):
        rate = max(15.0, 95.0 - no * 2.7)  # 뒤 번호일수록 정답률 하락
        conn.execute(
            """INSERT INTO problems (exam_id, number, points, answer, answer_rate,
                                     difficulty, unit, topic)
               VALUES (?,?,?,?,?,?,?,?)""",
            (exam_id, no, 4 if no > 20 else 3, str(no % 5 + 1), rate,
             dbm.difficulty_from_rate(rate), units[no % 5], f"{units[no % 5]} 유형{no % 3 + 1}"),
        )
conn.commit()

built = build_exam(conn, grade=1, count=20, seed=42)
assert len(built.problems) == 20, f"20문항이어야 하는데 {len(built.problems)}문항"

seq = [p.difficulty for p in built.problems]
print("난이도 배열:", " ".join(seq))
print("난이도 상승 지점:", built.ramp_points)
order = {"하": 0, "중": 1, "상": 2}
assert [order[d] for d in seq] == sorted(order[d] for d in seq), "난이도가 오름차순이 아님"
assert seq.count("상") >= 3, "상 난이도 문항 부족"

freqs = [p.frequency for p in built.problems]
assert all(f is not None and 0 < f <= 100 for f in freqs), f"출제율 계산 실패: {freqs}"
print("출제율 예시:", freqs[:5])

html = render_html(built, title="고1 수학 스모크 테스트", problems_dir=Path("."),
                   show_difficulty=True, show_frequency=True, mark_ramp=True, show_source=True)
out = Path(__file__).parent / "smoke_output.html"
out.write_text(html, encoding="utf-8")
assert "난이도" in html and "출제율" in html and "구간이 시작" in html
print(f"HTML 생성 확인 → {out}")
print("\n[성공] 전체 흐름 정상 동작")
