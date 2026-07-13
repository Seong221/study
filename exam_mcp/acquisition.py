"""시험 수집 경로 안내(라우팅).

'○○년 ○월 시험을 구해줘' 요청에 대해, 이 서버의 어떤 도구로 자동 수집이
가능한지 / 어디부터 사용자가 직접 받아야 하는지를 판정해 절차를 반환한다.

클라이언트(카카오톡 AI 등)가 수집 경로를 스스로 추론하지 못해도
plan_acquisition 결과의 '절차'를 순서대로 따라 하면 되도록 설계했다.

배경 지식 (2026-07 조사 결과):
- 평가원(KICE) 주관은 고3 6월·9월 모의평가와 11월 수능뿐이다.
  수능은 suneung.re.kr 기출 게시판에서 문제지+정답표 자동 수집이 가능하다.
- 그 외 달은 교육청 주관 전국연합학력평가(학평)다. 저작권 정책 변경 이후
  교육청들은 문제지를 공개 배포하지 않으므로(서울·부산·인천 게시판 확인)
  문제지 원본은 EBSi 로그인 다운로드만 가능하고, 정답표·해설·통계는
  서울시교육청 자료실에서 자동 수집할 수 있다.
- 정책 변경 전의 과거 시험은 인천교육청 게시판에 문답지(문제지+답지)
  zip이 남아 있는 경우가 있다.
- 사설 모의고사(대성·종로 등)는 저작권 문제로 수집을 지원하지 않는다.
"""
from __future__ import annotations

EBSI_URL = "https://www.ebsi.co.kr/ebs/xip/xipc/previousPaperList.ebs"

#: 학년별 학평(전국연합학력평가) 시행 월. 고3 4월/5월은 연도에 따라 한쪽만 시행.
HAKPYEONG_MONTHS = {1: (3, 6, 9, 11), 2: (3, 6, 9, 11), 3: (3, 4, 5, 7, 10)}
MOPYEONG_MONTHS = (6, 9)  # 평가원 모의평가 (고3)
SUNEUNG_MONTH = 11        # 수능 (고3)

_PRIVATE_NOTE = "사설 모의고사(대성·종로 등)는 저작권 문제로 수집을 지원하지 않습니다."


def _ebsi_manual_steps(year: int, month: int, grade: int, source: str) -> list[str]:
    fname = f"{year}_{month:02d}_g{grade}_math.pdf"
    return [
        f"   [사용자 직접] EBSi 기출문제실({EBSI_URL})에 로그인해 "
        f"{year}년 {month}월 고{grade} 수학 문제지 PDF를 다운로드",
        f"   [사용자 직접] 받은 PDF를 서버의 data/raw/ 폴더에 저장 (예: {fname})",
        f"   [자동] ingest_local_pdf(filename='{fname}', source='{source}', "
        f"year={year}, month={month}, grade={grade}) → 문항 분리·DB 등록",
    ]


def plan_acquisition(year: int, month: int, grade: int) -> dict:
    """시행 연도·월·학년으로 시험을 분류하고 수집 절차를 반환한다."""
    req = f"{year}년 {month}월 고{grade} 수학"
    if grade not in (1, 2, 3):
        return {"요청": req, "오류": "grade는 1/2/3 중 하나여야 합니다."}
    if not 1 <= month <= 12:
        return {"요청": req, "오류": "month는 1~12 사이여야 합니다."}

    if grade == 3 and month == SUNEUNG_MONTH:
        hakneyndo = year + 1
        return {
            "요청": req,
            "분류": f"대학수학능력시험 ({hakneyndo}학년도 수능, 평가원 주관)",
            "연도_해석": f"입력한 year는 시행연도로 해석됨: {year}년 11월 시행 = {hakneyndo}학년도 수능. "
                       f"사용자가 '{year}학년도 수능'을 의미했다면 year={year - 1}로 다시 호출하세요.",
            "자동_수집": "전부 자동 (문제지+정답표)",
            "빠른_길": f"이 학년도 하나만 원하면 acquire_suneung(hakneyndo={hakneyndo}) 호출 한 번이면 됩니다. "
                     "최신 수능 여러 개를 한꺼번에 채우려면 bootstrap_bank(n_exams=개수).",
            "절차": [
                f"1. [자동] acquire_suneung(hakneyndo={hakneyndo}) → 게시글 탐색·다운로드·문항 분리·정답 등록까지 자동",
                "2. [자동] list_exams()로 등록 확인",
            ],
        }

    if grade == 3 and month in MOPYEONG_MONTHS:
        hakneyndo = year + 1
        return {
            "요청": req,
            "분류": f"평가원 {month}월 모의평가 ({hakneyndo}학년도, 평가원 주관)",
            "연도_해석": f"입력한 year는 시행연도로 해석됨: {year}년 {month}월 시행 = {hakneyndo}학년도 모평.",
            "자동_수집": "불가 - 평가원 수능 홈페이지에 모의평가 문제지 게시판이 없음(2026-07 확인). "
                       "문제지는 EBSi 로그인 다운로드만 가능",
            "사용자_안내문": (
                f"{hakneyndo}학년도 {month}월 모의평가({year}년 {month}월 시행) 문제지는 "
                f"평가원이 공개 게시하지 않아 제가 직접 가져올 수 없어요. "
                f"EBSi 기출문제실({EBSI_URL})에서 무료 로그인 후 바로 내려받을 수 있습니다. "
                "이 안내문을 사용자에게 그대로 전달하세요."
            ),
            "절차": [
                "1. 위 '사용자_안내문'을 사용자에게 그대로 전달 (다운로드는 사용자 본인이 EBSi에서)",
                "2. (로컬 서버 운영자 전용) 받은 PDF를 문제은행에 등록하려면:",
                *_ebsi_manual_steps(year, month, 3, source="KICE"),
            ],
        }

    if month in HAKPYEONG_MONTHS[grade]:
        note = " (고3 4월/5월 학평은 연도에 따라 한쪽만 시행)" if grade == 3 and month in (4, 5) else ""
        return {
            "요청": req,
            "분류": f"전국연합학력평가 (학평, 교육청 주관){note}",
            "자동_수집": "부분 자동 - 정답표·해설·통계는 자동, 문제지 원본은 사용자가 EBSi에서 직접",
            "사용자_안내문": (
                f"{year}년 {month}월 고{grade} 학력평가 문제지는 교육청이 공개 배포하지 않아 "
                f"제가 직접 가져올 수 없어요. EBSi 기출문제실({EBSI_URL})에서 무료 로그인 후 "
                "바로 내려받을 수 있습니다. 이 안내문을 사용자에게 그대로 전달하세요."
            ),
            "절차": [
                "① 문제지 (저작권 정책상 교육청 공개 배포 없음 → 사용자 직접):",
                *_ebsi_manual_steps(year, month, grade, source="OFFICE"),
                "② 정답표·해설 (자동):",
                f"   [자동] discover_hakpyeong() → '{year}년 {month}월 고{grade} … 정답표' zip의 url을 "
                "acquire_hakpyeong_file(url, name)로 다운로드 (최근 시험만 첫 페이지에 노출됨)",
                "③ 과거 시험이라면 문제지도 자동 수집 가능성 있음 (선택):",
                "   [자동] discover_incheon() 또는 웹 검색으로 ice.go.kr 게시글 번호(nttSn)를 찾아 "
                "incheon_post_files(ntt_sn) 조회 → 문답지 zip이 있으면 acquire_hakpyeong_file로 다운로드",
                "④ 등록 마무리 (자동):",
                "   [자동] 정답표를 view_answer_sheet(filename=...)로 읽고 "
                "set_answers(exam_id, answers_json)로 정답·배점 입력",
            ],
            "비고": "저작권 정책 변경 이후 서울·부산·인천 등 교육청은 학평 문제지를 게시하지 않습니다"
                    f" (2026-07 확인). {_PRIVATE_NOTE}",
        }

    return {
        "요청": req,
        "분류": "해당 월에 시행되는 공식 시험이 없습니다",
        "자동_수집": "불가",
        "시행_달력": {
            "고1·고2 학평": "3·6·9·11월",
            "고3 학평": "3월, 4월 또는 5월, 7월, 10월",
            "고3 평가원": "6월 모평 · 9월 모평 · 11월 수능",
        },
        "비고": _PRIVATE_NOTE,
    }
