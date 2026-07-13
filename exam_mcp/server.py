"""기출 문제은행 MCP 서버.

Claude가 이 서버의 tool을 호출해 기출 검색·문제지 생성·메타데이터 태깅을 수행한다.
실행:  python -m exam_mcp.server  (stdio)
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP, Image

from . import db as dbm
from .acquisition import plan_acquisition as _plan_acquisition
from .discover import incheon_attachments, list_attachments, list_board, list_hakpyeong, list_incheon
from .ingest.fetch import extract_pdfs
from .exam_builder import build_exam
from .ingest.answers import apply_answers, apply_answers_text, apply_triplets
from .ingest.fetch import SecurityError, fetch, manifest_url_for
from .ingest.split_pdf import split_pdf
from .render import render_html

DATA_DIR = dbm.DATA_DIR
PROBLEMS_DIR = DATA_DIR / "problems"
OUTPUT_DIR = DATA_DIR / "output"

mcp = FastMCP(
    "gichul-exam-bank",
    instructions=(
        "평가원·교육청 수학 기출 문제은행. 표준 사용 순서: "
        "1) list_exams로 보유 시험 확인 - 비어 있으면 bootstrap_bank() 호출(최근 수능 자동 수집, "
        "네트워크 오류가 나면 같은 호출을 한 번 더 시도). "
        "2) generate_exam(grade=학년, count=문항수)으로 문제지 생성. "
        "3) 정답이 비어 있으면 사용자에게 정답 목록을 붙여넣어 달라고 요청한 뒤, "
        "받은 텍스트를 가공 없이 set_answers_text에 전달. "
        "연도 표기 주의: 수능은 학년도가 시행연도보다 1 크다 (2026학년도 수능 = 2025년 11월 시행). "
        "각 도구 응답 끝의 안내 문장을 그대로 따르면 된다."
    ),
)


def _conn():
    return dbm.connect()


@mcp.tool()
def list_exams() -> str:
    """문제은행에 등록된 시험 목록과 문항 수를 반환한다."""
    conn = _conn()
    rows = conn.execute(
        """SELECT e.id, e.title, e.source, e.year, e.month, e.grade, e.track, e.source_url,
                  COUNT(p.id) AS n_problems,
                  SUM(CASE WHEN p.difficulty IS NOT NULL THEN 1 ELSE 0 END) AS n_rated,
                  SUM(CASE WHEN p.unit IS NOT NULL THEN 1 ELSE 0 END) AS n_tagged
           FROM exams e LEFT JOIN problems p ON p.exam_id = e.id
           GROUP BY e.id ORDER BY e.year, e.month, e.grade"""
    ).fetchall()
    if not rows:
        return ("등록된 시험이 없습니다. bootstrap_bank()를 호출하면 최근 수능 수학 기출을 "
                "자동 수집합니다. 특정 시험은 plan_acquisition(year, month, grade)으로 경로를 확인하세요.")
    out = []
    for r in rows:
        d = dict(r)
        d["출처"] = dbm.site_name(d.pop("source_url"))
        # 평가원 시험은 학년도 병기 (2026학년도 수능 = 2025년 11월 시행) - 연도 혼동 방지
        if d["source"] == "KICE":
            d["학년도"] = f"{d['year'] + 1}학년도 (시행 {d['year']}년 {d['month']}월)"
        out.append(d)
    return json.dumps(out, ensure_ascii=False, indent=1)


@mcp.tool()
def search_problems(
    grade: int,
    unit: str | None = None,
    topic: str | None = None,
    difficulty: str | None = None,
    untagged_only: bool = False,
    limit: int = 30,
) -> str:
    """조건에 맞는 기출 문제를 검색한다.

    Args:
        grade: 학년 (1/2/3)
        unit: 대단원 필터 (예: '이차함수')
        topic: 세부 유형 필터
        difficulty: '상'/'중'/'하'
        untagged_only: 단원 태그가 없는 문제만 (태깅 작업용)
        limit: 최대 개수
    """
    conn = _conn()
    sql = """SELECT p.id, e.title AS exam, e.source_url, p.number, p.points, p.answer,
                    p.answer_rate, p.difficulty, p.unit, p.topic, p.image_path
             FROM problems p JOIN exams e ON e.id = p.exam_id WHERE e.grade=?"""
    params: list = [grade]
    for col, val in (("unit", unit), ("topic", topic), ("difficulty", difficulty)):
        if val:
            sql += f" AND p.{col}=?"
            params.append(val)
    if untagged_only:
        sql += " AND p.unit IS NULL"
    sql += " ORDER BY e.year, e.month, p.number LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    if not rows:
        return "결과 없음"
    out = []
    for r in rows:
        d = dict(r)
        d["출처"] = dbm.site_name(d.pop("source_url"))
        out.append(d)
    return json.dumps(out, ensure_ascii=False, indent=1)


@mcp.tool()
def view_problem(problem_id: int) -> Image:
    """문제 이미지를 보여준다. 태깅(단원/유형/난이도 판단) 작업 시 사용."""
    conn = _conn()
    row = conn.execute("SELECT image_path FROM problems WHERE id=?", (problem_id,)).fetchone()
    if not row or not row["image_path"]:
        raise ValueError(f"문제 {problem_id}의 이미지가 없습니다.")
    path = (PROBLEMS_DIR / row["image_path"]).resolve()
    # 경로 탈출 방어: DB 값이 오염되어도 문제 이미지 폴더 밖은 절대 읽지 않는다
    if not path.is_relative_to(PROBLEMS_DIR.resolve()):
        raise ValueError(f"허용되지 않은 경로입니다: {row['image_path']}")
    if not path.exists():
        raise ValueError(f"이미지 파일이 없습니다: {path}")
    return Image(path=str(path))


@mcp.tool()
def tag_problem(
    problem_id: int,
    unit: str | None = None,
    topic: str | None = None,
    points: int | None = None,
    answer: str | None = None,
    difficulty: str | None = None,
) -> str:
    """문제에 메타데이터를 부여한다. view_problem으로 문제를 읽고 판단한 값을 저장할 때 사용.

    difficulty를 직접 주지 않아도 answer_rate가 등록되면 자동 산출된다.
    """
    conn = _conn()
    updates, params = [], []
    for col, val in (("unit", unit), ("topic", topic), ("points", points),
                     ("answer", answer), ("difficulty", difficulty)):
        if val is not None:
            updates.append(f"{col}=?")
            params.append(val)
    if not updates:
        return "변경할 값이 없습니다."
    params.append(problem_id)
    conn.execute(f"UPDATE problems SET {', '.join(updates)} WHERE id=?", params)
    conn.commit()
    return f"문제 {problem_id} 업데이트 완료: {', '.join(updates)}"


@mcp.tool()
def set_answer_rate(problem_id: int, answer_rate: float) -> str:
    """문제의 정답률(%)을 등록하고 난이도(상/중/하)를 자동 산출한다."""
    conn = _conn()
    diff = dbm.difficulty_from_rate(answer_rate)
    conn.execute(
        "UPDATE problems SET answer_rate=?, difficulty=? WHERE id=?",
        (answer_rate, diff, problem_id),
    )
    conn.commit()
    return f"문제 {problem_id}: 정답률 {answer_rate}% → 난이도 '{diff}'"


@mcp.tool()
def frequency_stats(grade: int, subject: str = "수학") -> str:
    """학년별 유형 출제율 통계: 각 유형이 전체 시험 중 몇 %의 시험에서 출제됐는지."""
    conn = _conn()
    total = conn.execute(
        "SELECT COUNT(*) FROM exams WHERE grade=? AND subject=?", (grade, subject)
    ).fetchone()[0]
    if not total:
        return "해당 학년의 시험이 없습니다."
    rows = conn.execute(
        """SELECT p.unit, p.topic, COUNT(DISTINCT e.id) AS n_exams, COUNT(p.id) AS n_problems
           FROM problems p JOIN exams e ON e.id = p.exam_id
           WHERE e.grade=? AND e.subject=? AND p.topic IS NOT NULL
           GROUP BY p.unit, p.topic ORDER BY n_exams DESC""",
        (grade, subject),
    ).fetchall()
    out = [
        {**dict(r), "출제율(%)": round(r["n_exams"] / total * 100, 1)}
        for r in rows
    ]
    return json.dumps({"전체 시험 수": total, "유형별 통계": out}, ensure_ascii=False, indent=1)


@mcp.tool()
def plan_acquisition(year: int, month: int, grade: int) -> str:
    """새 시험(수능/모평/학평)을 문제은행에 추가하기 전, 수집 경로를 판정한다.

    '○○년 ○월 고○ 시험 구해줘/등록해줘/다운로드해줘' 요청을 받으면 다른 도구를
    호출하기 전에 반드시 이 도구를 먼저 호출할 것. 시험을 수능/모평/학평으로 분류하고,
    자동 수집 가능한 단계(어떤 도구를 어떤 인자로)와 사용자가 EBSi에서 직접 받아야
    하는 단계를 구분한 절차를 반환한다. 절차를 순서대로 따라 하면 된다.

    Args:
        year: 시행 연도 (예: 2025년 10월 시행 시험이면 2025. '학년도'가 아님 -
              학년도로 물었다면 수능·모평은 학년도-1이 시행 연도)
        month: 시행 월 (1~12)
        grade: 학년 (1/2/3)
    """
    return json.dumps(_plan_acquisition(year, month, grade), ensure_ascii=False, indent=1)


@mcp.tool()
def discover_exams(page: int = 1) -> str:
    """평가원 수능 기출문제 게시판을 탐색해 학년도·영역별 게시글 목록을 반환한다.

    학년도 N = 시행 (N-1)년 11월. 다운로드하려면 board_seq를 acquire_exam에 넘긴다.
    """
    posts = list_board(page)
    if not posts:
        return "게시글을 찾지 못했습니다. 게시판 구조가 바뀌었을 수 있습니다."
    return json.dumps(posts, ensure_ascii=False, indent=1)


@mcp.tool()
def acquire_exam(board_seq: int, hakneyndo: int, auto_split: bool = True) -> str:
    """게시글의 첨부 PDF(문제지 홀수형 + 정답표)를 보안 검사 후 다운로드하고,
    문제지는 자동으로 문항 분리해 DB에 등록한다. (수능 = 고3, 시행 11월)

    보안: 공식 도메인 화이트리스트 + PDF 능동 콘텐츠 검사를 통과한 파일만 저장된다.

    Args:
        board_seq: discover_exams가 반환한 게시글 번호
        hakneyndo: 학년도 (예: 2025) - 시행연도는 자동으로 (학년도-1)로 계산
        auto_split: 문제지를 바로 문항 이미지로 분리해 DB 등록할지
    """
    attachments = list_attachments(board_seq)
    if not attachments:
        return "첨부파일을 찾지 못했습니다."

    # 문제지(짝수형 제외)와 정답표만 선택
    targets = [
        a for a in attachments
        if a["filename"].lower().endswith(".pdf")
        and ("문제지" in a["filename"] or "정답" in a["filename"])
        and "짝수" not in a["filename"]
    ]
    if not targets:
        return "문제지/정답표 PDF가 없습니다. 첨부 목록: " + json.dumps(attachments, ensure_ascii=False)

    results = []
    exam_pdf = answers_pdf = exam_url = answers_url = None
    for a in targets:
        name = f"{hakneyndo}_{a['filename']}"
        dest = dbm.DATA_DIR / "raw" / name
        if dest.exists():
            results.append(f"[건너뜀] {name} - 이미 다운로드되어 있음 (오류 아님)")
        else:
            try:
                dest = fetch(a["url"], name)
                results.append(f"[저장] {name}")
            except SecurityError as e:
                results.append(f"[차단] {name}: {e}")
                continue
        if "문제지" in a["filename"]:
            exam_pdf, exam_url = dest, a["url"]
        elif "정답" in a["filename"]:
            answers_pdf, answers_url = dest, a["url"]

    if auto_split and exam_pdf:
        exam_id = split_pdf(exam_pdf, source="KICE", year=hakneyndo - 1, month=11, grade=3,
                            source_url=exam_url)
        results.append(
            f"[분리] {exam_pdf.name} → 시험 id={exam_id}로 문항 이미지 등록 완료 "
            f"({hakneyndo}학년도 수능 = {hakneyndo - 1}년 11월 시행, 고3)"
        )
        if answers_pdf:
            results.append(apply_answers(exam_id, answers_pdf))
        links = [f"문제지 {exam_url}"] + ([f"정답표 {answers_url}"] if answers_url else [])
        results.append(
            "[공식 다운로드 링크] " + " · ".join(links)
            + " ← 사용자가 원본 PDF를 원하면 이 평가원 공식 링크를 그대로 전달하세요."
        )
        results.append("다음: 문제지가 필요하면 generate_exam(grade=3)을 호출하세요.")

    return "\n".join(results)


@mcp.tool()
def bootstrap_bank(n_exams: int = 3) -> str:
    """문제은행이 비어 있을 때(새 배포 직후) 최근 수능 수학 기출을 자동 수집해 채운다.

    평가원 기출 게시판에서 최신 학년도부터 '수학' 게시글 n_exams개를 찾아
    acquire_exam 파이프라인(다운로드→문항 분리→정답 등록)을 차례로 실행한다.
    이미 등록된 시험은 자동으로 건너뛰므로 여러 번 호출해도 안전하다.
    학평(교육청) 문제지는 EBSi 수동 경로라 포함되지 않는다 - plan_acquisition 참고.

    generate_exam은 같은 학년의 서로 다른 시험 3개 이상을 요구하므로
    n_exams는 3 이상이어야 한다 (수능은 모두 고3).

    Args:
        n_exams: 수집할 수능 개수 (최신 학년도부터, 기본 3 = generate_exam 최소 요건)
    """
    results: list[str] = []
    found = 0
    for page in range(1, 6):
        posts = list_board(page)
        if not posts:
            break
        for p in posts:
            if found >= n_exams:
                break
            if "수학" not in p["subject"]:
                continue
            results.append(
                f"=== {p['hakneyndo']}학년도(={p['hakneyndo'] - 1}년 11월 시행) 수능 수학 "
                f"(board_seq={p['board_seq']}) ==="
            )
            results.append(acquire_exam(p["board_seq"], p["hakneyndo"]))
            found += 1
        if found >= n_exams:
            break
    if not found:
        return "평가원 게시판에서 수학 게시글을 찾지 못했습니다. 게시판 구조가 바뀌었을 수 있습니다."
    results.append(
        f"완료: 수능 {found}개 처리. list_exams()로 확인 후 generate_exam(grade=3)으로 "
        "즉시 문제지를 만들 수 있습니다 (배점·난이도는 문제지 본문에서 자동 추출됨). "
        "정답표 자동 파싱이 실패한 시험은 사용자에게 정답 목록(예: 1③ 2⑤ …)을 "
        "붙여넣어 달라고 요청한 뒤 그 텍스트를 그대로 set_answers_text에 넘기세요."
    )
    return "\n".join(results)


@mcp.tool()
def discover_hakpyeong() -> str:
    """서울시교육청 학력평가자료실을 탐색해 전국연합학력평가(고1·고2·고3) 자료 목록을 반환한다.

    이 게시판에는 정답표·해설·통계자료(zip)만 있다. 문제지 원본은 EBSi 로그인이 필요해
    자동 수집이 불가 - 사용자가 직접 받아 data/raw/에 넣으면 ingest_local_pdf로 등록한다.
    """
    posts = list_hakpyeong()
    if not posts:
        return "게시글을 찾지 못했습니다. 게시판 구조가 바뀌었을 수 있습니다."
    return json.dumps(posts, ensure_ascii=False, indent=1)


@mcp.tool()
def discover_incheon(page: int = 1) -> str:
    """인천광역시교육청 학력평가자료 게시판을 탐색한다.

    과거 게시글에는 학평 '문답지(문제지+답지)' zip이 있다 - 학평 문제지 원본을 구할 수 있는 경로.
    최근 글은 저작권 정책 변경으로 통계자료만 있을 수 있음. 게시글의 첨부는 incheon_post_files로 조회.
    원하는 시험이 목록에 없으면 웹 검색으로 ice.go.kr의 게시글(nttSn)을 찾아도 된다.
    """
    posts = list_incheon(page)
    if not posts:
        return "게시글을 찾지 못했습니다."
    return json.dumps(posts, ensure_ascii=False, indent=1)


@mcp.tool()
def incheon_post_files(ntt_sn: int) -> str:
    """인천 교육청 게시글의 첨부파일 목록을 반환한다. url을 acquire_hakpyeong_file에 넘겨 다운로드."""
    files = incheon_attachments(ntt_sn)
    if not files:
        return "첨부파일이 없습니다."
    return json.dumps(files, ensure_ascii=False, indent=1)


@mcp.tool()
def acquire_hakpyeong_file(url: str, name: str) -> str:
    """학평 자료(zip/pdf)를 보안 검사 후 다운로드한다. zip이면 내부 PDF를 추출해 목록을 반환.

    url은 discover_hakpyeong이 반환한 files[].url. 화이트리스트 밖 도메인은 차단된다.
    """
    try:
        dest = fetch(url, name)
    except SecurityError as e:
        return f"[차단] {e}"
    result = [f"[저장] {dest.name}"]
    if dest.suffix == ".zip":
        pdfs = extract_pdfs(dest)
        result.append(f"[추출] PDF {len(pdfs)}개 → data/raw/{dest.stem}/")
        result += [f"  - {p.name}" for p in pdfs]
    return "\n".join(result)


@mcp.tool()
def ingest_local_pdf(
    filename: str,
    source: str,
    year: int,
    month: int,
    grade: int,
    subject: str = "수학",
    track: str = "",
    start_page: int = 1,
) -> str:
    """data/raw/에 있는 문제지 PDF를 문항 이미지로 분리해 DB에 등록한다.

    사용자가 EBSi 등에서 직접 받아 넣은 학평 문제지를 등록할 때 사용.
    filename은 data/raw/ 기준 상대경로. source는 'KICE'(평가원) 또는 'OFFICE'(교육청 학평).
    """
    raw_dir = (DATA_DIR / "raw").resolve()
    path = (raw_dir / filename).resolve()
    if not path.is_relative_to(raw_dir):
        return f"허용되지 않은 경로입니다: {filename}"
    if not path.exists():
        return f"파일이 없습니다: data/raw/{filename}"
    if source not in ("KICE", "OFFICE"):
        return "source는 'KICE' 또는 'OFFICE'여야 합니다."
    exam_id = split_pdf(
        path, source=source, year=year, month=month, grade=grade,
        subject=subject, track=track, start_page=start_page,
        source_url=manifest_url_for(filename),
    )
    n = _conn().execute("SELECT COUNT(*) FROM problems WHERE exam_id=?", (exam_id,)).fetchone()[0]
    return (
        f"시험 id={exam_id}로 {n}문항 등록. "
        f"정답표가 있으면 view_answer_sheet로 읽고 set_answers로 입력하세요."
    )


@mcp.tool()
def view_answer_sheet(filename: str, page: int = 1) -> Image:
    """data/raw/에 저장된 정답표 PDF의 한 페이지를 이미지로 보여준다.

    정답표가 벡터/스캔 PDF라 자동 파싱이 안 될 때, 이 이미지를 읽고
    set_answers로 정답·배점을 입력하는 용도. (수능 정답표는 1쪽=홀수형)
    """
    import pymupdf

    raw_dir = (DATA_DIR / "raw").resolve()
    path = (raw_dir / filename).resolve()
    if not path.is_relative_to(raw_dir):
        raise ValueError(f"허용되지 않은 경로입니다: {filename}")
    if not path.exists():
        raise ValueError(f"파일이 없습니다: {filename}")
    doc = pymupdf.open(path)
    try:
        pix = doc[page - 1].get_pixmap(matrix=pymupdf.Matrix(2, 2))
        out = OUTPUT_DIR / "_answer_sheet_preview.png"
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        pix.save(out)
    finally:
        doc.close()
    return Image(path=str(out))


@mcp.tool()
def set_answers(exam_id: int, answers_json: str) -> str:
    """시험 전체의 정답·배점을 한 번에 입력한다 (잠정 난이도 자동 부여).

    answers_json 형식: {"1": ["5", 2], "2": ["4", 2], ..., "30": ["19", 4]}
    - 키: 문항 번호, 값: [정답(객관식은 1~5, 주관식은 답 숫자), 배점]
    - 배점 합계가 100점이 아니거나 번호가 빠지면 저장을 거부한다.
    """
    data = json.loads(answers_json)
    triplets = {int(k): (str(v[0]), int(v[1])) for k, v in data.items()}
    return apply_triplets(exam_id, triplets)


@mcp.tool()
def set_answers_text(exam_id: int, text: str) -> str:
    """사용자가 붙여넣은 정답 목록 텍스트를 그대로 전달하면 서버가 해석해 입력한다.

    JSON으로 바꾸거나 형식을 정리할 필요 없음 - 받은 텍스트를 가공 없이 넘기면 된다.
    "1③ 2⑤ 3④ …", "1번 3, 2번 5", 정답표 표 복사본(번호 정답 배점) 모두 허용.
    배점이 포함돼 있으면 배점까지, 아니면 정답만 기록한다(배점은 기존 값 유지).
    응답에 해석 결과가 들어 있으니 사용자에게 보여주고 확인받을 것.

    Args:
        exam_id: list_exams의 시험 id
        text: 사용자가 붙여넣은 정답 목록 원문
    """
    return apply_answers_text(exam_id, text)


@mcp.tool()
def generate_exam(
    grade: int,
    count: int = 20,
    units: list[str] | None = None,
    source: str | None = None,
    show_difficulty: bool = False,
    show_frequency: bool = False,
    mark_ramp: bool = False,
    title: str | None = None,
    seed: int | None = None,
) -> str:
    """평가원 스타일 문제지를 생성한다. 기출에서 난이도 곡선(쉬움→어려움)에 맞춰 선별하고
    HTML 문제지 파일로 저장한 뒤, 구성 요약과 파일 경로를 반환한다.

    문제지는 반드시 서로 다른 시험 3개 이상에서 구성되며, 조건에 맞는 시험이
    3개 미만이면 생성을 거부한다. 각 문항 아래에는 출처(원 시험·번호)가 항상 표시된다.

    Args:
        grade: 학년 (1/2/3)
        count: 문항 수
        units: 대단원 필터 (예: ['이차함수', '도형의 방정식'])
        source: 'KICE'(평가원만) / 'OFFICE'(학평만) / None(전체)
        show_difficulty: 문항마다 난이도 배지 표시
        show_frequency: 문항마다 유형 출제율 배지 표시
        mark_ramp: 난이도가 올라가는 지점을 문제지에 표시
        seed: 재현용 랜덤 시드
    """
    conn = _conn()
    built = build_exam(conn, grade=grade, count=count, units=units, source=source, seed=seed)
    if not built.problems:
        if built.notes:
            return " ".join(built.notes)
        return "조건에 맞는 문제가 없습니다. list_exams / frequency_stats로 보유 현황을 확인하세요."

    # 출처 수집: 선택된 문제들이 속한 원시험 + 다운로드한 공식 사이트
    exam_ids = sorted({
        conn.execute("SELECT exam_id FROM problems WHERE id=?", (p.problem_id,)).fetchone()[0]
        for p in built.problems
    })
    sources = []
    for eid in exam_ids:
        e = conn.execute("SELECT title, source_url FROM exams WHERE id=?", (eid,)).fetchone()
        sources.append({
            "title": e["title"],
            "site": dbm.site_name(e["source_url"]) or ("한국교육과정평가원" if "평가원" in e["title"] else "교육청"),
            "url": e["source_url"],
        })

    exam_title = title or f"고{grade} 수학 모의 문제지"
    html_doc = render_html(
        built, title=exam_title, problems_dir=PROBLEMS_DIR,
        show_difficulty=show_difficulty, show_frequency=show_frequency,
        mark_ramp=mark_ramp, sources=sources,
    )
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"exam_g{grade}_{count}q_{seed if seed is not None else 'r'}.html"
    n = 1
    while out_path.exists():
        out_path = OUTPUT_DIR / f"exam_g{grade}_{count}q_{n}.html"
        n += 1
    out_path.write_text(html_doc, encoding="utf-8")

    public_base = os.environ.get("GICHUL_PUBLIC_URL", "").rstrip("/")
    summary = {
        "문제지 파일": str(out_path),
        **({"문제지 URL": f"{public_base}/exams/{out_path.name}"} if public_base else {}),
        "출처": [
            f"{s['title']} — {s['site']}" + (f" ({s['url']})" if s["url"] else "")
            for s in sources
        ],
        "문항 수": len(built.problems),
        "난이도 상승 지점": {k: f"{v}번부터" for k, v in built.ramp_points.items()},
        "구성": [
            {
                "번호": p.position,
                "난이도": p.difficulty,
                "단원": p.unit,
                "유형": p.topic,
                "출제율(%)": p.frequency,
                "배점": p.points,
                "정답": p.answer,
                "출처": f"{p.exam_title} {p.original_number}번",
            }
            for p in built.problems
        ],
        "비고": built.notes,
    }
    return json.dumps(summary, ensure_ascii=False, indent=1)


@mcp.custom_route("/exams/{filename}", methods=["GET"])
async def serve_exam(request):
    """생성된 문제지 HTML을 원격 사용자에게 서빙한다 (HTTP 모드 전용)."""
    from starlette.responses import FileResponse, PlainTextResponse

    filename = request.path_params["filename"]
    out_dir = OUTPUT_DIR.resolve()
    path = (out_dir / filename).resolve()
    if not path.is_relative_to(out_dir) or path.suffix != ".html":
        return PlainTextResponse("잘못된 요청입니다.", status_code=400)
    if not path.exists():
        return PlainTextResponse("문제지를 찾을 수 없습니다.", status_code=404)
    return FileResponse(path, media_type="text/html")


# 추론이 약한 클라이언트(카카오 PlayMCP 등)용 코어 도구 모음.
# GICHUL_CORE_TOOLS=1이면 이것만 노출한다 - 선택지가 적을수록 약한 AI의 오호출이 줄어든다.
# 나머지(게시판 탐색 세부, 이미지 보기, JSON 정답 입력, 태깅 등)는 개발자용.
CORE_TOOLS = {
    "list_exams", "search_problems", "frequency_stats", "plan_acquisition",
    "discover_exams", "acquire_exam", "bootstrap_bank",
    "set_answers_text", "generate_exam",
}

if os.environ.get("GICHUL_CORE_TOOLS", "").lower() in ("1", "true", "yes"):
    for _name in list(mcp._tool_manager._tools):
        if _name not in CORE_TOOLS:
            del mcp._tool_manager._tools[_name]


if __name__ == "__main__":
    mcp.run()
