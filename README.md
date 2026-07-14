# 기출 문제은행 MCP 서버 (gichul-mcp)

**고3 수학 · 수능 기출 전용** 문제은행 MCP 서버.

평가원 수능 수학 기출을 **공식 출처(수능 홈페이지)에서 AI가 직접 수집**해 문제은행을 만들고,
난이도 곡선(쉬움→어려움)에 맞춰 재구성한 모의 문제지를 링크로 제공한다.

- 매년 새 수능이 공개되면 대화 한 마디로 자동 수집 (빈 문제은행도 스스로 채움)
- 특정 학년도 지정 수집 — 2021학년도 이전 구 체제(가형/나형)까지
- 생성 문제지: 하/중/상 난이도 구간 표시, 문항별 출처·배점, 브라우저 열람·인쇄
- 원본 문제지·정답표는 평가원 공식 다운로드 링크로 안내

> **범위**: 대상은 **고3 수학(수능)** 이다. 6·9월 모의평가와 고1·고2 학력평가 문제지는
> 저작권 정책상 공개 배포되지 않으므로(2026-07 출제기관 전수 확인) 자동 수집하지 않으며,
> 요청 시 EBSi 공식 경로를 안내한다.

## 도구 (PlayMCP 노출 기준, `GICHUL_CORE_TOOLS=1`)

| Tool | 기능 |
|---|---|
| `bootstrap_bank` | 빈 문제은행에 최신 수능 수학 n개를 자동 수집 (멱등, 재시도 안전) |
| `acquire_suneung` | 특정 학년도 수능 수집 (구 체제 가형/나형 자동 처리, 원본 공식 링크 반환) |
| `discover_exams` / `acquire_exam` | 평가원 기출 게시판 탐색 / 게시글 단위 수집 |
| `generate_exam` | 난이도 곡선 문제지 생성 → 공개 URL 반환 (`/exams/<파일>` 서빙) |
| `set_answers_text` | 사용자가 붙여넣은 정답 목록 원문을 서버가 해석해 입력 |
| `list_exams` / `search_problems` / `frequency_stats` | 보유 현황·검색·유형 통계 |
| `plan_acquisition` | 연도·월·학년으로 시험을 분류하고 수집 가능 여부·안내문 반환 |

개발자용 도구(문항 이미지 보기, 태깅, JSON 정답 입력, 교육청 게시판 세부 탐색)는
`GICHUL_CORE_TOOLS=0`일 때 전체 노출된다.

## 구조

```
gichul-mcp/
├── run_server.py              # 실행 진입점 (stdio / streamable HTTP)
├── Dockerfile                 # PlayMCP(카카오 클라우드) Git 소스 빌드용
├── exam_mcp/
│   ├── db.py                  # SQLite 스키마 (exams, problems)
│   ├── discover.py            # 평가원 게시판 탐색 (학년도 검색 포함)
│   ├── acquisition.py         # 수집 경로 라우팅 (수능/모평/학평 분류·안내문)
│   ├── exam_builder.py        # 난이도 곡선 선별 (서로 다른 시험 3개 이상 강제)
│   ├── render.py              # HTML 문제지 (구간 표시·인쇄 버튼·출처 고지)
│   ├── server.py              # MCP tool 정의
│   └── ingest/
│       ├── fetch.py           # 안전 다운로더 (화이트리스트 + PDF 검사 + 재시도)
│       ├── split_pdf.py       # PDF → 문항 이미지 분리 + 배점 추출 + 잠정 난이도
│       └── answers.py         # 정답표 파싱 / 자유 형식 정답 텍스트 해석
└── data/                      # 저장소에 포함되지 않음 (아래 저작권 참고)
```

## 실행

**원격 (PlayMCP / 카카오 클라우드)** — Git 소스 빌드(Dockerfile):
- MCP 엔드포인트: `https://<호스트>/mcp` (streamable HTTP)
- 문제지 공개 URL은 요청 Host 헤더에서 자동 감지 (`GICHUL_PUBLIC_URL`로 강제 지정 가능)
- 컨테이너 재시작으로 문제은행이 비면 `bootstrap_bank()` 한 번으로 복구

**로컬 (Claude Code 등)**: `python run_server.py` (stdio)

## 저작권·출처 설계

- **기출 데이터(`data/`)는 저장소·도커 이미지에 포함되지 않는다.** 배포된 서버가
  평가원 공식 사이트에서 직접 수집해 채운다.
- 모든 시험에 수집 출처 URL을 기록하고, 문제지 HTML과 응답 하단에
  **자료 출처(기관명+URL) + 저작권 고지**를 항상 표시한다.
- 원본 PDF는 재배포하지 않고 평가원 공식 다운로드 링크로만 안내한다.
- 다운로드는 어떤 경로든 공식 도메인 화이트리스트 + https + PDF 능동 콘텐츠 검사
  (fetch.py)를 통과해야 저장된다 — AI가 임의 URL을 요청해도 차단된다.

## 보안

- PDF 검사: 매직바이트·파싱 검증, JavaScript/Launch/임베디드 파일 등 능동 콘텐츠 거부,
  암호화 PDF 거부, 크기 상한, SHA-256 manifest 기록(`fetch verify`로 변조 감지)
- zip 검사: 경로 탈출 차단, 실행파일 거부, 압축 폭탄 방어
- 모든 SQL 파라미터 바인딩, 파일 서빙(`/exams`)은 경로 탈출 차단 + HTML만
- 일시적 네트워크 오류는 서버가 자동 재시도(백오프)로 흡수

## 검증

```
.venv\Scripts\python scripts\smoke_test.py
```

## 로드맵 (PlayMCP)

- [x] 수능 자동 수집: 최신 일괄(bootstrap) + 학년도 지정(acquire_suneung, 구 체제 포함)
- [x] 문제지 생성: 난이도 구간 표시·인쇄·공개 링크 서빙 (Host 자동 감지)
- [x] 추론이 약한 클라이언트 대응: 코어 도구 축소, 응답마다 다음 행동 안내,
      자유 형식 정답 입력(set_answers_text), 학년도/시행연도 병기
- [x] 출처·저작권 고지 시스템, 기출 데이터 무포함 배포
- [ ] 수능 선택과목(23~30번) 확률과통계 외 미적분·기하 track 수집
- [ ] PlayMCP 심사 제출
