# 기출 문제은행 MCP 서버 (gichul-mcp)

평가원·교육청 수학 기출문제를 수집·분석해 저장하고, AI(Claude)가 "고1 수학 20문제 출제해줘" 같은
요청에 평가원 스타일 문제지를 만들어 주는 MCP 서버.

## 구조

```
gichul-mcp/
├── run_server.py              # MCP 서버 실행 진입점
├── exam_mcp/
│   ├── db.py                  # SQLite 스키마 (exams, problems)
│   ├── exam_builder.py        # 난이도 곡선 기반 문제 선별 + 출제율 계산
│   ├── render.py              # HTML 문제지 렌더링
│   ├── server.py              # MCP tool 정의
│   └── ingest/
│       ├── split_pdf.py       # PDF → 문제 단위 이미지 분리
│       └── import_meta.py     # 정답/배점/정답률/단원 CSV 일괄 등록
├── data/
│   ├── raw/                   # 다운로드한 기출 PDF를 여기에
│   ├── problems/              # 분리된 문제 이미지 (시험id/문항번호.png)
│   ├── output/                # 생성된 문제지 HTML
│   └── exam.db                # 문제은행 DB (자동 생성)
└── scripts/smoke_test.py      # 전체 흐름 검증
```

## 설치 (완료됨)

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

## 데이터 구축 워크플로

### 1. 기출 PDF 수집

- **평가원(고3)**: 수능·6월·9월 모의평가 → [수능 홈페이지 기출문제](https://www.suneung.re.kr) 에서 PDF 공개
- **교육청 학평(고1·고2·고3)**: 전국연합학력평가 → 서울시교육청 학력평가 자료실, EBSi 기출문제실

**안전 다운로더 사용** (직접 브라우저로 받아 `data/raw/`에 넣어도 됨):

```
.venv\Scripts\python -m exam_mcp.ingest.fetch "https://www.suneung.re.kr/boardCnts/fileDown.do?fileSeq=..." --name 2026-suneung-math.pdf
```

저장 전에 자동으로 검사한다:
- 도메인 화이트리스트 (평가원/교육청/EBS 공식 도메인 + https만 허용, 리다이렉트 이탈 차단)
- PDF 형식 검증 (매직바이트, 파싱 가능 여부, 크기 20KB~80MB)
- 능동 콘텐츠 거부 (JavaScript, Launch, 임베디드 파일 등 악성 PDF 요소)
- zip 검사: 경로 탈출 항목 차단, 문서 형식 외(실행파일 등) 거부, 압축 폭탄 방어(해제 총량 상한),
  추출된 개별 PDF도 동일 검사
- 암호화 PDF 거부, SHA-256 해시를 `data/raw/manifest.json`에 기록

> **저작권**: 평가원·교육청 기출은 개인 학습 목적 이용은 자유롭지만, 상업적 이용·재배포에는
> 제한이 있다. 개인용/교육용 범위에서 사용할 것.

### 2. 문제 단위로 분리

```
.venv\Scripts\python -m exam_mcp.ingest.split_pdf data\raw\2025-03-g1-math.pdf ^
    --source OFFICE --year 2025 --month 3 --grade 1
```

- `--source KICE`(평가원) / `OFFICE`(교육청 학평), 고3 선택과목 시험지는 `--track 미적분` 등 지정
- 분리 결과를 `data/problems/<시험id>/`에서 눈으로 확인. 한 문제가 단을 넘어가는 경우
  뒷부분이 잘릴 수 있음 → 이미지 편집으로 수정 후 같은 파일명으로 덮어쓰기

### 3. 메타데이터 등록 (두 가지 방법)

**방법 A — CSV 일괄 등록** (정답률은 EBSi 등에서 공개하는 문항별 정답률 사용):

```csv
source,year,month,grade,subject,track,number,answer,points,answer_rate,unit,topic
OFFICE,2025,3,1,수학,,1,3,2,94.2,다항식,다항식의 연산
OFFICE,2025,3,1,수학,,21,7,4,18.5,이차함수,이차함수 최대최소
```

```
.venv\Scripts\python -m exam_mcp.ingest.import_meta data\raw\meta.csv
```

**방법 B — Claude에게 태깅 시키기**: MCP 연결 후 Claude에게
"태그 안 된 문제를 `search_problems(untagged_only=true)`로 찾아서 `view_problem`으로 보고
`tag_problem`으로 단원/유형을 채워줘" 라고 요청. Claude가 문제 이미지를 읽고 직접 분류한다.

- 정답률 → 난이도 자동 매핑: **80%↑ = 하, 40~80% = 중, 40%↓ = 상** (`db.py`에서 조정 가능)
- 출제율 = 해당 유형이 출제된 시험 수 ÷ 전체 시험 수 (같은 학년·과목 기준)

## 실행 모드

**로컬 (Claude Code)**: `D:\Claude\.mcp.json`에 등록되어 있어 새 세션에서 자동 연결 (stdio).

**원격 (PlayMCP/카카오 클라우드 등)**:
```
GICHUL_PUBLIC_URL=https://<공개주소> python run_server.py --http --port 8000
```
- MCP 엔드포인트: `http://<호스트>:8000/mcp` (streamable HTTP)
- 생성된 문제지는 `/exams/<파일명>`으로 서빙되며, `GICHUL_PUBLIC_URL` 설정 시
  generate_exam 응답에 사용자용 문제지 URL이 포함됨

## 출처 표기

- 모든 시험은 다운로드한 공식 사이트 URL(`source_url`)을 기록
- `generate_exam` 응답과 문제지 HTML 하단에 **자료 출처(기관명 + URL) + 저작권 고지**가 항상 표시됨
- `list_exams`/`search_problems`에도 출처 필드 포함

## Claude Code 연결

새 세션에서 바로 사용 가능:

| Tool | 기능 |
|---|---|
| `discover_exams` | 평가원 수능 기출문제 게시판 자동 탐색 (학년도·영역별 게시글 목록) |
| `acquire_exam` | 수능 문제지·정답표 자동 다운로드 + 문항 분리 + 정답 자동 입력(텍스트 정답표인 경우) |
| `discover_hakpyeong` | 서울시교육청 학력평가자료실 탐색 (학평 정답표·해설·통계 zip) |
| `acquire_hakpyeong_file` | 학평 자료 다운로드 (zip 보안 검사 + 내부 PDF 추출) |
| `ingest_local_pdf` | 사용자가 직접 받은 문제지 PDF(EBSi 학평 등)를 문항 분리해 등록 |
| `view_answer_sheet` / `set_answers` | 벡터/스캔 정답표를 AI가 읽고 정답·배점 일괄 입력 (배점 합계 100점 검증) |
| `generate_exam` | 평가원 스타일 문제지 생성 (난이도 곡선, 출제율/난이도 배지, 난이도 상승 지점 표시 옵션) |

**주의 - 학평 문제지**: 전국연합학력평가 문제지 원본은 EBSi 로그인이 필요해 자동 수집이 안 된다.
사용자가 EBSi에서 받아 `data/raw/`에 넣으면 AI가 `ingest_local_pdf`로 등록한다.
정답표·해설은 서울시교육청 자료실에서 자동 수집 가능.
| `search_problems` | 학년/단원/유형/난이도로 기출 검색 |
| `view_problem` | 문제 이미지 보기 (태깅용) |
| `tag_problem` / `set_answer_rate` | 메타데이터 입력 (정답률 입력 시 난이도 자동 산출) |
| `frequency_stats` | 학년별 유형 출제율 통계 |
| `list_exams` | 보유 시험 현황 |

사용 예: *"고1 수학 20문제 출제해줘. 난이도 올라가는 지점 표시하고 각 문제에 출제율도 붙여줘"*
→ Claude가 `generate_exam(grade=1, count=20, mark_ramp=true, show_frequency=true)` 호출
→ `data/output/`에 인쇄 가능한 HTML 문제지 생성

## 보안

**다운로드 시** (fetch.py): 도메인 화이트리스트 + PDF 능동 콘텐츠 검사 (위 "1. 기출 PDF 수집" 참고)

**저장 후**:
- 변조 감지: `.venv\Scripts\python -m exam_mcp.ingest.fetch verify`
  → `data/raw/`의 모든 파일을 다운로드 당시 SHA-256과 대조. 새 파일을 받은 뒤나 주기적으로 실행 권장.
- Windows Defender 정밀 검사:
  `& "C:\Program Files\Windows Defender\MpCmdRun.exe" -Scan -ScanType 3 -File "D:\Claude\gichul-mcp\data"`

**설계상 안전장치**:
- AI가 `discover_exams`/`acquire_exam`으로 직접 탐색·다운로드할 수 있지만, **어떤 경로로든 다운로드는
  반드시 fetch.py의 보안 검사(공식 도메인 화이트리스트 + https + PDF 능동 콘텐츠 검사)를 통과해야 저장됨** —
  임의 URL은 AI가 요청해도 차단된다
- MCP 서버는 네트워크 접근·셸 실행 코드가 전혀 없음 (DB 읽기/쓰기 + HTML 파일 생성만)
- 모든 SQL은 파라미터 바인딩 (인젝션 방지), `view_problem`은 문제 이미지 폴더 밖 경로 접근 차단
- 문제지는 실행 코드 없는 정적 HTML로만 출력

## 검증

```
.venv\Scripts\python scripts\smoke_test.py
```

## 로드맵

- [x] 실제 기출 PDF 1회분으로 분리 품질 검증 및 splitter 보정 (2026학년도 수능 수학 30문항 확인)
- [x] 2026 수능 정답·배점 입력 (배점 합계 100점 교차검증), 킬러문항(21·22·30)은 실측 정답률, 나머지는 배점·배치 기반 잠정 난이도
- [ ] EBSi 로그인 후 문항별 정답률을 받아 잠정 난이도를 실측으로 교체 (`set_answer_rate` 호출 시 자동 갱신)
- [ ] 고3 선택과목 구분: 수능 PDF의 23~30번은 확통/미적/기하가 반복되는데 현재는 첫 과목만 수집됨
- [ ] EBSi 정답률 자동 수집
- [ ] 수식 텍스트(LaTeX) 변환 파이프라인 (이미지 → 텍스트 점진 전환)
- [ ] 국어·영어 등 타 과목 확장
