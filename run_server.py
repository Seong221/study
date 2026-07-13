"""MCP 서버 실행 진입점. 어느 경로에서 실행해도 동작하도록 sys.path를 고정한다.

로컬(Claude Code 등):  python run_server.py                → stdio
원격(PlayMCP/카카오 클라우드 등): python run_server.py --http --port 8000
  → streamable HTTP. MCP 엔드포인트는 http://<호스트>:<포트>/mcp,
    생성된 문제지는 /exams/<파일명> 에서 서빙된다.
  → 환경변수 GICHUL_PUBLIC_URL(예: https://my-server.kr-central.kakaocloud.com)을
    설정하면 generate_exam 응답에 사용자용 문제지 URL이 포함된다.
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from exam_mcp.server import mcp

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--http", action="store_true", help="원격용 streamable HTTP 모드")
    ap.add_argument("--host", default="0.0.0.0")
    # 클라우드 빌드 환경이 PORT 환경변수로 포트를 지정하는 경우를 지원
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    args = ap.parse_args()
    if args.http:
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        # SDK의 DNS 리바인딩 방어는 localhost 계열 Host 헤더만 허용해서, 공개 도메인
        # (PlayMCP 인그레스) 뒤에서는 모든 요청이 421 Invalid Host header로 거부된다.
        # 이 방어는 로컬에서 도는 서버용이므로 HTTP 모드에서는 끈다.
        # 특정 호스트만 허용하려면 GICHUL_ALLOWED_HOSTS=host1,host2 로 지정.
        from mcp.server.transport_security import TransportSecuritySettings

        allowed = os.environ.get("GICHUL_ALLOWED_HOSTS")
        if allowed:
            hosts = [h.strip() for h in allowed.split(",") if h.strip()]
            mcp.settings.transport_security = TransportSecuritySettings(allowed_hosts=hosts)
        else:
            mcp.settings.transport_security = TransportSecuritySettings(
                enable_dns_rebinding_protection=False
            )
        mcp.run(transport="streamable-http")
    else:
        mcp.run()
