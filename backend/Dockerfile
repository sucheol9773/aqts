# ══════════════════════════════════════
# Stage 1: Builder — 의존성 설치
# ══════════════════════════════════════
FROM python:3.11.9-slim-bookworm AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY requirements.txt .
# torch CPU 전용 빌드 먼저 설치 (GPU 불필요, 이미지 ~2GB 절감)
RUN pip install --no-cache-dir --upgrade pip==24.0 \
    && pip install --no-cache-dir --prefix=/install \
       torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir --prefix=/install -r requirements.txt


# ══════════════════════════════════════
# Stage 2: Runtime — 최소 이미지
# ══════════════════════════════════════
FROM python:3.11.9-slim-bookworm

LABEL maintainer="AQTS Project"

# 런타임 시스템 의존성만 설치 (build-essential 제외)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Non-root 사용자 생성
RUN groupadd --gid 1000 appuser \
    && useradd --uid 1000 --gid 1000 --create-home appuser

# Builder에서 설치된 패키지 복사
COPY --from=builder /install /usr/local

WORKDIR /app

# 소스 코드 복사 후 소유권 변경
COPY --chown=appuser:appuser . .

# Non-root 사용자로 전환
USER appuser

# 헬스체크
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:8000/api/system/health || exit 1

EXPOSE 8000

# 프로덕션: --reload 제거, 워커 수 환경변수로 제어
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", "--loop", "uvloop", "--http", "httptools"]
