"""문제지 HTML 렌더링 (평가원 표지 느낌, 인쇄 가능)."""
from __future__ import annotations

import base64
import html
from pathlib import Path

from .exam_builder import BuiltExam

DIFF_COLOR = {"하": "#2e7d32", "중": "#e65100", "상": "#b71c1c"}


def _img_data_uri(image_path: Path) -> str | None:
    if not image_path.exists():
        return None
    b64 = base64.b64encode(image_path.read_bytes()).decode()
    return f"data:image/png;base64,{b64}"


def render_html(
    built: BuiltExam,
    *,
    title: str,
    problems_dir: Path,
    show_difficulty: bool = False,
    show_frequency: bool = False,
    mark_ramp: bool = False,
    show_source: bool = False,
    sources: list[dict] | None = None,  # [{"title", "site", "url"}] 출처 표기 (항상 문서 하단에)
) -> str:
    parts: list[str] = []
    parts.append(f"""
<style>
  body {{ font-family: 'Malgun Gothic', sans-serif; max-width: 800px; margin: 0 auto; padding: 24px; }}
  .head {{ border: 2px solid #000; padding: 16px 20px; margin-bottom: 24px; }}
  .head h1 {{ margin: 0; font-size: 22px; text-align: center; letter-spacing: 8px; }}
  .head .sub {{ text-align: center; margin-top: 6px; color: #444; font-size: 13px; }}
  .prob {{ margin-bottom: 36px; page-break-inside: avoid; }}
  .prob .no {{ font-weight: bold; font-size: 17px; }}
  .badge {{ display: inline-block; font-size: 11px; padding: 1px 8px; border-radius: 10px;
            color: #fff; margin-left: 6px; vertical-align: 2px; }}
  .ramp {{ border-top: 2px dashed #b71c1c; color: #b71c1c; font-size: 13px;
           padding-top: 4px; margin: 28px 0 20px; }}
  .src {{ color: #888; font-size: 11px; margin-top: 4px; }}
  img.problem {{ max-width: 100%; display: block; margin-top: 8px; }}
  .missing {{ color: #999; font-style: italic; margin-top: 8px; }}
  @media print {{ .prob {{ break-inside: avoid; }} }}
</style>
<div class="head">
  <h1>{html.escape(title)}</h1>
  <div class="sub">총 {len(built.problems)}문항 · 기출 재구성</div>
</div>
""")

    ramp_positions = {v: k for k, v in built.ramp_points.items()} if mark_ramp else {}

    for p in built.problems:
        if p.position in ramp_positions:
            level = ramp_positions[p.position]
            parts.append(
                f'<div class="ramp">▲ 이 문항부터 난이도 \'{level}\' 구간이 시작됩니다</div>'
            )
        badges = ""
        if show_difficulty:
            color = DIFF_COLOR.get(p.difficulty, "#555")
            badges += f'<span class="badge" style="background:{color}">난이도 {p.difficulty}</span>'
        if show_frequency and p.frequency is not None:
            badges += f'<span class="badge" style="background:#1565c0">출제율 {p.frequency}%</span>'
        points = f" [{p.points}점]" if p.points else ""
        parts.append(f'<div class="prob"><span class="no">{p.position}.</span>{points}{badges}')
        img_uri = _img_data_uri(problems_dir / p.image_path) if p.image_path else None
        if img_uri:
            parts.append(f'<img class="problem" src="{img_uri}" alt="문제 {p.position}">')
        else:
            parts.append('<div class="missing">(문제 이미지 없음 — 원문 미등록)</div>')
        if show_source:
            parts.append(f'<div class="src">{html.escape(p.exam_title)} {p.original_number}번</div>')
        parts.append("</div>")

    # 출처·저작권 고지 (항상 표시)
    parts.append('<hr style="margin-top:40px"><div style="font-size:12px;color:#555">')
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
