"""기출 PDF 안전 다운로더.

공식 기관 도메인에서만 내려받고, 저장 전에 보안 검사를 통과해야 data/raw/에 들어간다.

검사 항목:
  1. 도메인 화이트리스트 (평가원/교육청/EBS 계열만 허용)
  2. PDF 매직바이트 + 실제 파싱 가능 여부 + 페이지 수
  3. 크기 상한 (기본 80MB)
  4. 능동 콘텐츠: JavaScript, OpenAction/AA(자동실행), Launch, 임베디드 파일 → 발견 시 거부
  5. 암호화된 PDF 거부
  6. SHA-256 해시를 data/raw/manifest.json에 기록

사용:
  python -m exam_mcp.ingest.fetch <URL> [--name 2025-03-g1-math.pdf]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import pymupdf

from .. import db as dbm

RAW_DIR = dbm.DATA_DIR / "raw"
MANIFEST = RAW_DIR / "manifest.json"
MAX_SIZE = 80 * 1024 * 1024  # 80MB
MIN_SIZE = 20 * 1024         # 20KB (정상 시험지가 이보다 작을 수 없음)

# 공식 기관 도메인만 허용 (서브도메인 포함)
ALLOWED_DOMAINS = (
    "kice.re.kr",      # 한국교육과정평가원
    "suneung.re.kr",   # 수능 홈페이지 (평가원)
    "ebsi.co.kr",      # EBSi 기출문제실
    "ebs.co.kr",
    "sen.go.kr",       # 서울특별시교육청
    "goe.go.kr",       # 경기도교육청
    "pen.go.kr",       # 부산광역시교육청
    "ice.go.kr",       # 인천광역시교육청
    "dge.go.kr",       # 대구광역시교육청
    "cbe.go.kr",       # 충청북도교육청
    "sje.go.kr",       # 세종특별자치시교육청
)

# 악성 PDF에서 쓰이는 능동 콘텐츠 마커
ACTIVE_CONTENT_MARKERS = [b"/JavaScript", b"/JS(", b"/JS<", b"/Launch", b"/EmbeddedFile", b"/RichMedia"]
AUTO_ACTION_MARKERS = [b"/OpenAction", b"/AA"]


class SecurityError(Exception):
    pass


# zip 내부에 허용되는 문서 확장자 (실행 가능한 것은 전부 거부)
ZIP_ALLOWED_EXT = {".pdf", ".hwp", ".hwpx", ".doc", ".docx", ".xls", ".xlsx", ".txt"}
ZIP_MAX_UNCOMPRESSED = 300 * 1024 * 1024  # 압축 해제 총량 상한 (zip 폭탄 방어)


def check_zip(path: Path) -> list[str]:
    """zip 보안 검사: 경로 탈출, 실행파일, 압축 폭탄을 차단한다."""
    import zipfile

    warnings: list[str] = []
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as e:
        raise SecurityError(f"zip 파싱 실패: {e}") from e
    with zf:
        total = 0
        for info in zf.infolist():
            name = info.filename
            if name.startswith(("/", "\\")) or ".." in name.replace("\\", "/").split("/"):
                raise SecurityError(f"경로 탈출 시도 항목: {name!r}")
            total += info.file_size
            if total > ZIP_MAX_UNCOMPRESSED:
                raise SecurityError("압축 해제 크기가 상한을 초과합니다 (zip 폭탄 의심).")
            if info.is_dir():
                continue
            ext = Path(name).suffix.lower()
            if ext not in ZIP_ALLOWED_EXT:
                raise SecurityError(f"허용되지 않은 파일 형식이 zip에 포함됨: {name!r}")
        if zf.testzip() is not None:
            warnings.append("zip 무결성 검사에서 손상된 항목이 있습니다.")
    return warnings


def extract_pdfs(zip_path: Path) -> list[Path]:
    """검사를 통과한 zip에서 PDF만 data/raw/<zip이름>/ 아래로 추출한다."""
    import zipfile

    out_dir = RAW_DIR / zip_path.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    extracted = []
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir() or Path(info.filename).suffix.lower() != ".pdf":
                continue
            safe_name = re.sub(r'[\\/:*?"<>|]', "_", Path(info.filename).name)
            dest = out_dir / safe_name
            dest.write_bytes(zf.read(info))
            check_pdf(dest)  # 개별 PDF도 동일 검사
            extracted.append(dest)
    return extracted


def check_domain(url: str) -> None:
    host = (urlparse(url).hostname or "").lower()
    if not any(host == d or host.endswith("." + d) for d in ALLOWED_DOMAINS):
        raise SecurityError(
            f"허용되지 않은 도메인: {host}\n"
            f"공식 기관 도메인만 허용됩니다: {', '.join(ALLOWED_DOMAINS)}"
        )
    if urlparse(url).scheme != "https":
        raise SecurityError("https URL만 허용됩니다.")


def check_pdf(path: Path) -> list[str]:
    """PDF 보안 검사. 치명적 문제는 SecurityError, 경미한 것은 경고 목록으로 반환."""
    warnings: list[str] = []
    data = path.read_bytes()

    if len(data) < MIN_SIZE:
        raise SecurityError(f"파일이 너무 작습니다 ({len(data)} bytes) - 오류 페이지일 가능성.")
    if len(data) > MAX_SIZE:
        raise SecurityError(f"파일이 너무 큽니다 ({len(data) / 1e6:.0f}MB > 상한 {MAX_SIZE / 1e6:.0f}MB).")
    if not data.startswith(b"%PDF-"):
        head = data[:200].decode("utf-8", errors="replace")
        raise SecurityError(f"PDF가 아닙니다. 파일 시작부: {head!r}")

    for marker in ACTIVE_CONTENT_MARKERS:
        if marker in data:
            raise SecurityError(
                f"능동 콘텐츠 발견: {marker.decode()} - 시험지 PDF에는 있을 이유가 없는 요소라 저장을 거부합니다."
            )
    for marker in AUTO_ACTION_MARKERS:
        if marker in data:
            warnings.append(f"자동 실행 힌트({marker.decode()}) 존재 - 대개 '열면 1쪽으로 이동' 수준이지만 확인 권장.")

    try:
        doc = pymupdf.open(path)
    except Exception as e:
        raise SecurityError(f"PDF 파싱 실패: {e}") from e
    try:
        if doc.is_encrypted:
            raise SecurityError("암호화된 PDF는 저장하지 않습니다.")
        if doc.page_count < 1:
            raise SecurityError("페이지가 없는 PDF입니다.")
        if doc.page_count > 100:
            warnings.append(f"페이지 수가 많습니다({doc.page_count}쪽) - 시험지 묶음인지 확인하세요.")
    finally:
        doc.close()
    return warnings


def record_manifest(dest: Path, url: str, warnings: list[str]) -> None:
    entries = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else []
    entries.append({
        "file": dest.name,
        "url": url,
        "sha256": hashlib.sha256(dest.read_bytes()).hexdigest(),
        "size": dest.stat().st_size,
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "warnings": warnings,
    })
    MANIFEST.write_text(json.dumps(entries, ensure_ascii=False, indent=1), encoding="utf-8")


def fetch(url: str, name: str | None = None) -> Path:
    """URL을 검증→다운로드→보안 검사 후 data/raw/에 저장하고 경로를 반환한다."""
    check_domain(url)
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (gichul-mcp exam fetcher)"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        # 리다이렉트로 화이트리스트 밖으로 나가는 것 차단
        check_domain(resp.url)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp_path = Path(tmp.name)
            while chunk := resp.read(1 << 20):
                tmp.write(chunk)
                if tmp.tell() > MAX_SIZE:
                    raise SecurityError("다운로드 중 크기 상한 초과 - 중단.")
        # 파일명 결정: 인자 > Content-Disposition > URL 경로
        if not name:
            cd = resp.headers.get("Content-Disposition", "")
            m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', cd)
            name = m.group(1) if m else Path(urlparse(url).path).name or "download.pdf"

    is_zip = tmp_path.read_bytes()[:4] == b"PK\x03\x04"
    try:
        warnings = check_zip(tmp_path) if is_zip else check_pdf(tmp_path)
    except SecurityError:
        tmp_path.unlink(missing_ok=True)
        raise

    ext = ".zip" if is_zip else ".pdf"
    if not name.lower().endswith(ext):
        name += ext
    name = re.sub(r'[\\/:*?"<>|]', "_", name)  # 경로 조작 문자 제거
    dest = RAW_DIR / name
    dest.write_bytes(tmp_path.read_bytes())
    tmp_path.unlink(missing_ok=True)
    record_manifest(dest, url, warnings)

    print(f"[저장] {dest} ({dest.stat().st_size / 1e6:.1f}MB)")
    for w in warnings:
        print(f"[경고] {w}")
    return dest


def manifest_url_for(filename: str) -> str | None:
    """data/raw/ 파일이 어느 URL에서 왔는지 manifest에서 찾는다.

    zip에서 추출된 PDF('묶음명/파일.pdf')는 원본 zip의 URL을 반환한다.
    """
    if not MANIFEST.exists():
        return None
    entries = json.loads(MANIFEST.read_text(encoding="utf-8"))
    by_file = {e["file"]: e["url"] for e in entries}
    name = filename.replace("\\", "/")
    if name in by_file:
        return by_file[name]
    if "/" in name:  # 추출된 PDF → 부모 zip
        return by_file.get(name.split("/")[0] + ".zip")
    return None


def verify_manifest() -> bool:
    """data/raw/의 파일들이 다운로드 당시 해시와 일치하는지 검사 (변조 감지)."""
    if not MANIFEST.exists():
        print("manifest.json이 없습니다. 아직 다운로드한 파일이 없습니다.")
        return True
    entries = json.loads(MANIFEST.read_text(encoding="utf-8"))
    ok = True
    for e in entries:
        path = RAW_DIR / e["file"]
        if not path.exists():
            print(f"[누락] {e['file']} - 파일이 삭제되었습니다.")
            ok = False
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != e["sha256"]:
            print(f"[변조 의심] {e['file']} - 다운로드 당시와 해시가 다릅니다!")
            print(f"  기록: {e['sha256']}")
            print(f"  현재: {actual}")
            ok = False
        else:
            print(f"[정상] {e['file']}")
    return ok


def main() -> None:
    ap = argparse.ArgumentParser(description="기출 PDF 안전 다운로드")
    ap.add_argument("url", help="다운로드할 URL, 또는 'verify' (저장된 파일 변조 검사)")
    ap.add_argument("--name", help="저장할 파일명 (예: 2025-03-g1-math.pdf)")
    args = ap.parse_args()
    if args.url == "verify":
        raise SystemExit(0 if verify_manifest() else 1)
    try:
        fetch(args.url, args.name)
    except SecurityError as e:
        raise SystemExit(f"[차단] {e}")


if __name__ == "__main__":
    main()
