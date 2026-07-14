"""문제지 HTML 렌더링 (A4 한 장에 4문항 배치, 인쇄 가능)."""
from __future__ import annotations

import base64
import html
from pathlib import Path

from .exam_builder import BuiltExam, SelectedProblem

DIFF_COLOR = {"하": "#2e7d32", "중": "#e65100", "상": "#b71c1c"}
PER_PAGE = 4  # A4 한 장에 2×2로 배치할 문항 수


def _img_data_uri(image_path: Path) -> str | None:
    if not image_path.exists():
        return None
    b64 = base64.b64encode(image_path.read_bytes()).decode()
    return f"data:image/png;base64,{b64}"


def _cell(
    p: SelectedProblem,
    *,
    problems_dir: Path,
    ramp_positions: dict[int, str],
    show_difficulty: bool,
    show_frequency: bool,
) -> str:
    parts: list[str] = ['<div class="cell">']
    if p.position in ramp_positions:
        level = ramp_positions[p.position]
        color = DIFF_COLOR.get(level, "#555")
        parts.append(
            f'<div class="ramp" style="border-color:{color};color:{color}">'
            f"▲ 이 문항부터 난이도 '{level}' 구간입니다</div>"
        )
    badges = ""
    if show_difficulty:
        color = DIFF_COLOR.get(p.difficulty, "#555")
        badges += f'<span class="badge" style="background:{color}">난이도 {p.difficulty}</span>'
    if show_frequency and p.frequency is not None:
        badges += f'<span class="badge" style="background:#1565c0">출제율 {p.frequency}%</span>'
    points = f" [{p.points}점]" if p.points else ""
    parts.append(f'<div class="prob-head"><span class="no">{p.position}.</span>{points}{badges}</div>')
    img_uri = _img_data_uri(problems_dir / p.image_path) if p.image_path else None
    if img_uri:
        parts.append(f'<img class="problem" src="{img_uri}" alt="문제 {p.position}">')
    else:
        parts.append('<div class="missing">(문제 이미지 없음 — 원문 미등록)</div>')
    # 출처는 항상 문항 아래에 표시
    parts.append(f'<div class="src">출처: {html.escape(p.exam_title)} {p.original_number}번</div>')
    parts.append("</div>")
    return "\n".join(parts)


def render_html(
    built: BuiltExam,
    *,
    title: str,
    problems_dir: Path,
    show_difficulty: bool = False,
    show_frequency: bool = False,
    mark_ramp: bool = False,
    sources: list[dict] | None = None,  # [{"title", "site", "url"}] 출처 표기 (항상 문서 하단에)
) -> str:
    parts: list[str] = []
    parts.append(f"""
<meta charset="utf-8">
<style>
  body {{ font-family: 'Malgun Gothic', sans-serif; margin: 0; padding: 16px 0;
          background: #e8e8e8; }}
  /* A4 시트: 화면에서는 종이 모양 카드, 인쇄에서는 페이지 1장 */
  .sheet {{ width: 210mm; min-height: 296mm; box-sizing: border-box; margin: 0 auto 16px;
            padding: 10mm 12mm; background: #fff; border: 1px solid #bbb;
            display: grid; grid-template-columns: 1fr 1fr; grid-auto-rows: 1fr;
            grid-auto-flow: column; grid-template-rows: 1fr 1fr;
            gap: 6mm 10mm; break-after: page; }}
  .sheet.first {{ grid-template-rows: auto 1fr 1fr; }}
  .head {{ grid-column: 1 / -1; grid-row: 1; border: 2px solid #000;
           padding: 10px 16px; align-self: start; }}
  .head h1 {{ margin: 0; font-size: 20px; text-align: center; letter-spacing: 8px; }}
  .head .sub {{ text-align: center; margin-top: 4px; color: #444; font-size: 12px; }}
  .cell {{ overflow: hidden; display: flex; flex-direction: column; min-height: 0;
           border-top: 1px solid #eee; padding-top: 4mm; }}
  .prob-head .no {{ font-weight: bold; font-size: 16px; }}
  .badge {{ display: inline-block; font-size: 10px; padding: 1px 7px; border-radius: 10px;
            color: #fff; margin-left: 5px; vertical-align: 2px; }}
  .ramp {{ border-top: 2px dashed; font-size: 11px; font-weight: bold;
           padding-top: 2px; margin-bottom: 4px; }}
  .src {{ color: #888; font-size: 10px; margin-top: auto; padding-top: 3px; }}
  img.problem {{ max-width: 100%; max-height: 105mm; width: auto; object-fit: contain;
                 object-position: left top; display: block; margin-top: 4px; }}
  .missing {{ color: #999; font-style: italic; margin-top: 8px; }}
  .notice {{ width: 210mm; box-sizing: border-box; margin: 0 auto; padding: 8mm 12mm;
             background: #fff; border: 1px solid #bbb; font-size: 12px; color: #555; }}
  .print-btn {{ position: fixed; top: 16px; right: 16px; padding: 8px 18px; font-size: 14px;
                font-family: inherit; border: 1px solid #333; background: #fff;
                border-radius: 6px; cursor: pointer; box-shadow: 0 1px 4px rgba(0,0,0,.15); }}
  .print-btn:hover {{ background: #eee; }}
  @page {{ size: A4; margin: 0; }}
  @media print {{
    body {{ background: #fff; padding: 0; }}
    .sheet {{ border: none; margin: 0; min-height: 0; height: 296mm; }}
    .notice {{ border: none; }}
    .print-btn {{ display: none; }}
  }}
</style>
<title>{html.escape(title)}</title>
<button class="print-btn" onclick="window.print()">&#128424; 인쇄</button>
""")

    ramp_positions = {v: k for k, v in built.ramp_points.items()} if mark_ramp else {}
    if mark_ramp and built.problems:
        # 첫 문항의 구간('하')도 표시한다. 하 문항이 없으면 첫 전환점이 1번이므로 덮어쓰지 않는다.
        ramp_positions.setdefault(built.problems[0].position, built.problems[0].difficulty)

    header = (
        f'<div class="head"><h1>{html.escape(title)}</h1>'
        f'<div class="sub">총 {len(built.problems)}문항 · 기출 재구성</div></div>'
    )

    # A4 한 장에 4문항씩 (첫 장은 표지 헤더 포함)
    for start in range(0, len(built.problems), PER_PAGE):
        chunk = built.problems[start:start + PER_PAGE]
        first = start == 0
        parts.append(f'<div class="sheet{" first" if first else ""}">')
        if first:
            parts.append(header)
        for p in chunk:
            parts.append(_cell(
                p, problems_dir=problems_dir, ramp_positions=ramp_positions,
                show_difficulty=show_difficulty, show_frequency=show_frequency,
            ))
        parts.append("</div>")

    # 출처·저작권 고지 (항상 표시)
    parts.append('<div class="notice">')
    parts.append("<b>자료 출처</b><ul>")
    for s in sources or []:
        site = html.escape(s.get("site") or "")
        url = s.get("url")
        link = f' — <a href="{html.escape(url)}">{html.escape(url)}</a>' if url else ""
        parts.append(f"<li>{html.escape(s['title'])} ({site}){link}</li>")
    parts.append("</ul>")
    parts.append(
        "<p>본 문제지는 위 기관이 공개한 기출문제를 재구성한 것이며, "
        "각 문제의 저작권은 해당 출제 기관에 있습니다. 개인 학습 목적 외의 "
        "무단 복제·배포를 금합니다.</p></div>"
    )
    return "\n".join(parts)
