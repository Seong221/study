# PlayMCP in KC (카카오 클라우드) Git 소스 빌드용.
# 컨테이너는 streamable HTTP 모드로 /mcp 엔드포인트를 연다.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# data/는 저장소에 포함되지 않는다(저작권) - 기동 시 자동 생성되고,
# bootstrap_bank / acquire_exam 도구로 클라우드에서 직접 수집해 채운다.
EXPOSE 8000
CMD ["python", "run_server.py", "--http"]
