# ══════════════════════════════════════
# Stage 1: Builder — 의존성 설치
# ══════════════════════════════════════
# rolling 3.11 태그 사용 — Python 패치(현재 3.11.14+) + Debian 12 보안 업데이트를
# 빌드 시점마다 자동 흡수. 빌드 산출물은 cosign 으로 digest 단위 서명되어
# 배포에서는 digest 가 고정된다.
FROM python:3.11-slim-bookworm AS builder

# OS 패키지 + 보안 패치 일괄 적용 (deb12u 보안 피드 흡수)
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
       build-essential \
       libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY requirements.txt .
# pip/setuptools/wheel 최신화 (setuptools/wheel 자체 CVE 대응)
# torch CPU 전용 빌드 먼저 설치 (GPU 불필요, 이미지 ~2GB 절감)
# stable-baselines3가 torch 의존성을 가지므로, 두 번째 pip install 시
# PYTHONPATH로 기존 torch 설치를 인식시키고 --extra-index-url로 CPU 버전을 우선하도록 설정
RUN pip install --no-cache-dir --upgrade pip "setuptools>=78.1.1" "wheel>=0.46.2" \
    && pip install --no-cache-dir --prefix=/install \
       torch --index-url https://download.pytorch.org/whl/cpu \
    && PYTHONPATH=/install/lib/python3.11/site-packages \
       pip install --no-cache-dir --prefix=/install \
       --extra-index-url https://download.pytorch.org/whl/cpu \
       -r requirements.txt


# ══════════════════════════════════════
# Stage 2: Runtime — 최소 이미지
# ══════════════════════════════════════
FROM python:3.11-slim-bookworm

LABEL maintainer="AQTS Project"

# OS 패키지 보안 패치 흡수 + 런타임 의존성만 설치 (build-essential 제외)
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
       libpq5 \
       curl \
    && rm -rf /var/lib/apt/lists/*

# 베이스 이미지에 기본 포함된 setuptools/wheel 의 CVE 대응 (시스템 site-packages)
RUN pip install --no-cache-dir --upgrade pip "setuptools>=78.1.1" "wheel>=0.46.2"

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
