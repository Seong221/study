"""문항 메타데이터(정답, 배점, 정답률, 단원/유형) CSV 일괄 등록.

CSV 형식 (UTF-8, 헤더 필수):
  source,year,month,grade,subject,track,number,answer,points,answer_rate,unit,topic

answer_rate가 있으면 난이도(상/중/하)가 자동 산출된다.
빈 칸은 건너뛴다(기존 값 유지).

사용:
  python -m exam_mcp.ingest.import_meta data/raw/meta_2024.csv
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from .. import db as dbm


def import_csv(csv_path: Path) -> None:
    conn = dbm.connect()
    updated = 0
    with open(csv_path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            exam_id = dbm.get_or_create_exam(
                conn,
                source=row["source"].strip(),
                year=int(row["year"]),
                month=int(row["month"]),
                grade=int(row["grade"]),
                subject=(row.get("subject") or "수학").strip(),
                track=(row.get("track") or "").strip(),
            )
            number = int(row["number"])
            conn.execute(
                "INSERT OR IGNORE INTO problems (exam_id, number) VALUES (?,?)",
                (exam_id, number),
            )
            sets, params = [], []
            for col in ("answer", "unit", "topic"):
                val = (row.get(col) or "").strip()
                if val:
                    sets.append(f"{col}=?")
                    params.append(val)
            if (row.get("points") or "").strip():
                sets.append("points=?")
                params.append(int(row["points"]))
            if (row.get("answer_rate") or "").strip():
                rate = float(row["answer_rate"])
                sets.append("answer_rate=?")
                params.append(rate)
                sets.append("difficulty=?")
                params.append(dbm.difficulty_from_rate(rate))
            if sets:
                params += [exam_id, number]
                conn.execute(
                    f"UPDATE problems SET {', '.join(sets)} WHERE exam_id=? AND number=?",
                    params,
                )
                updated += 1
    conn.commit()
    print(f"[완료] {updated}개 문항 메타데이터 반영")


def main() -> None:
    ap = argparse.ArgumentParser(description="문항 메타데이터 CSV 등록")
    ap.add_argument("csv", type=Path)
    args = ap.parse_args()
    import_csv(args.csv)


if __name__ == "__main__":
    main()
