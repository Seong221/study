"""평가원 수능 기출문제 게시판 자동 탐색.

MCP tool에서 호출되어 게시글 목록과 첨부파일을 찾아낸다.
모든 HTTP 요청은 fetch.py의 도메인 화이트리스트를 통과해야 하며,
다운로드 자체는 fetch.fetch()의 보안 검사를 그대로 거친다.
"""
from __future__ import annotations

import re
import urllib.request

from .ingest.fetch import check_domain, open_with_retry

BOARD_BASE = "https://www.suneung.re.kr/boardCnts"
BOARD_ID = "1500234"  # 기출문제 게시판
MAX_HTML = 2 * 1024 * 1024  # 게시판 HTML 크기 상한 2MB


def http_get(url: str) -> str:
    """화이트리스트 검증 후 HTML을 가져온다 (일시적 네트워크 오류는 자동 재시도)."""
    check_domain(url)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (gichul-mcp exam fetcher)"})
    with open_with_retry(req, timeout=30) as resp:
        check_domain(resp.url)
        data = resp.read(MAX_HTML)
    return data.decode("utf-8", errors="replace")


def _strip_tags(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def list_board(page: int = 1) -> list[dict]:
    """기출문제 게시판 목록 (최신순).

    반환: [{board_seq, hakneyndo(학년도), subject(영역)}, ...]
    학년도 N = 시행 (N-1)년 11월 수능. 예: 2026학년도 → year=2025, month=11.
    """
    url = f"{BOARD_BASE}/list.do?boardID={BOARD_ID}&m=0403&s=suneung&page={page}"
    html = http_get(url)
    posts = []
    for m in re.finditer(
        r"<td>(\d{4})</td>\s*<td>([^<]+)</td>\s*<td[^>]*>\s*<a[^>]*goView\('%s','(\d+)'" % BOARD_ID,
        html,
    ):
        posts.append({
            "hakneyndo": int(m.group(1)),
            "subject": m.group(2).strip(),
            "board_seq": int(m.group(3)),
        })
    return posts


def find_suneung_post(hakneyndo: int, max_pages: int = 20) -> dict | None:
    """기출 게시판을 페이지 순회하며 특정 학년도의 수학 게시글을 찾는다.

    게시판은 최신순이므로, 현재 페이지의 최소 학년도가 목표보다 작아지면 중단한다.
    """
    for page in range(1, max_pages + 1):
        posts = list_board(page)
        if not posts:
            return None
        for p in posts:
            if p["hakneyndo"] == hakneyndo and "수학" in p["subject"]:
                return p
        if min(p["hakneyndo"] for p in posts) < hakneyndo:
            return None  # 이미 지나침 - 게시판에 없음
    return None


SEN_BOARD = "https://www.sen.go.kr/user/bbs/BD_selectBbsList.do?q_bbsSn=1036"


def list_hakpyeong() -> list[dict]:
    """서울시교육청 학력평가자료실 목록 (전국연합학력평가 정답표·해설·통계 zip).

    반환: [{title, files: [{filename, url}]}, ...] (최신순)
    주의: 문제지 원본은 이 게시판에 없다 (EBSi 로그인 필요) - 정답표/해설/통계만 제공.
    """
    html = http_get(SEN_BOARD)
    posts = []
    for row in re.findall(r"<tr>(.*?)</tr>", html, re.DOTALL):
        t = re.search(r"bbs_title[^>]*>\s*([^<]+)", row)
        if not t:
            continue
        files = [
            {
                "filename": m.group(2).strip(),
                "url": "https://www.sen.go.kr" + m.group(1).replace("&amp;", "&"),
            }
            for m in re.finditer(
                r'href="(/component/file/ND_fileDownload\.do[^"]+)"[^>]*title="([^"]+?)\s*다운로드', row
            )
        ]
        posts.append({"title": t.group(1).strip(), "files": files})
    return posts


ICE_LIST = "https://www.ice.go.kr/ice/na/ntt/selectNttList.do?mi=10910&bbsId=1703"
ICE_VIEW = "https://www.ice.go.kr/ice/na/ntt/selectNttInfo.do?mi=10910&nttSn={ntt_sn}"
ICE_FILE = "https://www.ice.go.kr/comm/nttFileDownload.do?fileKey={key}"


def list_incheon(page: int = 1) -> list[dict]:
    """인천광역시교육청 학력평가자료 게시판 목록.

    과거 게시글에는 학평 '문답지(문제지+답지)' zip이 첨부되어 있다.
    최근 글은 저작권 정책으로 문제지 없이 통계자료만 올라올 수 있음.
    반환: [{ntt_sn, title}, ...]
    """
    html = http_get(f"{ICE_LIST}&pageIndex={page}")
    return [
        {"ntt_sn": int(m.group(1)), "title": m.group(2).strip()}
        for m in re.finditer(r'data-id="(\d+)" class="nttInfoBtn" title="([^"]+)"', html)
    ]


def incheon_attachments(ntt_sn: int) -> list[dict]:
    """인천 게시글의 첨부파일 목록: [{filename, url}, ...]."""
    html = http_get(ICE_VIEW.format(ntt_sn=ntt_sn))
    files = []
    for m in re.finditer(
        r"goFileDown\('([0-9a-f]+)'\);\"[^>]*title=\"([^\"]+?)\s*다운로드\"", html
    ):
        files.append({
            "filename": m.group(2).strip(),
            "url": ICE_FILE.format(key=m.group(1)),
        })
    return files


def list_attachments(board_seq: int) -> list[dict]:
    """게시글의 첨부파일 목록: [{file_seq, filename, url}, ...]."""
    url = f"{BOARD_BASE}/view.do?boardID={BOARD_ID}&boardSeq={board_seq}&lev=0&m=0403&s=suneung"
    html = http_get(url)
    files = []
    seen = set()
    for m in re.finditer(
        r'fileDown\.do\?[^"\']*fileSeq=([0-9a-fA-F]+)[^"\']*["\'][^>]*>(.*?)</a>',
        html, re.DOTALL,
    ):
        seq, name = m.group(1), _strip_tags(m.group(2))
        if seq in seen:
            continue
        seen.add(seq)
        files.append({
            "file_seq": seq,
            "filename": name,
            "url": f"{BOARD_BASE}/fileDown.do?fileSeq={seq}",
        })
    return files
