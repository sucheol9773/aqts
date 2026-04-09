# ══════════════════════════════════════
# Stage 1: Builder — 의존성 설치
# ══════════════════════════════════════
# rolling 3.11 태그 사용 — Python 패치(현재 3.11.14+) + Debian 12 보안 업데이트를
# 빌드 시점마다 자동 흡수. 빌드 산출물은 cosign 으로 digest 단위 서명되어
# 배포에서는 digest 가 고정된다.
FROM python:3.11-slim-bookworm AS builder

# OS 패키지 + 보안 패치 일괄 적용 (deb12u 보안 피드 흡수)
# APT_UPGRADE_DATE 는 CI 에서 오늘 날짜(YYYY-MM-DD)로 주입되어 apt 레이어를
# 일(day)-단위로 cache-bust 한다. pip/torch 레이어 캐시는 유지하면서
# debian 보안 피드(deb12uX)는 하루 1회 확실히 흡수된다.
ARG APT_UPGRADE_DATE=unknown
RUN echo "apt-upgrade-date=${APT_UPGRADE_DATE}" \
    && apt-get update \
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
       -r requirements.txt \
    && rm -rf /install/lib/python3.11/site-packages/setuptools \
              /install/lib/python3.11/site-packages/setuptools-*.dist-info \
              /install/lib/python3.11/site-packages/pkg_resources \
              /install/lib/python3.11/site-packages/_distutils_hack \
              /install/lib/python3.11/site-packages/wheel \
              /install/lib/python3.11/site-packages/wheel-*.dist-info \
    && pip install --no-cache-dir --prefix=/install --upgrade --force-reinstall \
       "setuptools>=78.1.1" "wheel>=0.46.2"
# 마지막 줄: requirements.txt 설치 시 transitive 로 끌려온 구버전 setuptools/wheel 을
# /install 트리에서도 강제로 최신 패치 버전으로 덮어쓴다 (GHSA-5rjg-fvgr-3xxf,
# GHSA-8rrh-rw8j-w5fx 대응). 빌더 system python 업그레이드만으로는 /install 에
# 별도로 들어간 70.2.0 / 0.44.0 이 그대로 남아 grype 가 검출함.


# ══════════════════════════════════════
# Stage 2: Runtime — 최소 이미지
# ══════════════════════════════════════
FROM python:3.11-slim-bookworm

LABEL maintainer="AQTS Project"

# OS 패키지 보안 패치 흡수 + 런타임 의존성만 설치 (build-essential 제외)
# runtime stage 도 동일하게 APT_UPGRADE_DATE 로 apt 레이어만 cache-bust.
ARG APT_UPGRADE_DATE=unknown
RUN echo "apt-upgrade-date=${APT_UPGRADE_DATE}" \
    && apt-get update \
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
