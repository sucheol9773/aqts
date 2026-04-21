#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════
# Phase 3 contingency — CLI fallback for sigstore/cosign-installer
# ══════════════════════════════════════════════════════════════
#
# 적용 시점: 2026-08-01 에도 sigstore/cosign-installer 가 Node 20 런타임을
# 유지하고 있는 경우. 상세 절차: agent_docs/development-policies.md §13.1
# Phase 3.
#
# 참고: cd.yml 의 "Ensure cosign pinned" 블록에 이미 동일 로직(curl 직접
# 다운로드 + /usr/local/bin 설치 + 버전 어서트)이 존재한다. 본 스크립트는
# 해당 로직을 CI 재활용 가능한 단일 스크립트로 추출하여, CI sign /
# CD verify 양쪽이 동일 설치 경로를 공유하도록 한다 — signer/verifier
# 버전 불일치로 인한 "no signatures found" 실패를 방지.
#
# 사용:
#   bash scripts/ci/install_cosign.sh v3.0.5
#
# 기본값: v3.0.5 (cd.yml env.COSIGN_VERSION 과 동기화)
#
# 보안 주의:
#   - cosign 바이너리는 sigstore/cosign 공식 GitHub Releases 에서만 pull
#   - 설치 후 버전 어서트로 pin 을 강제 (지정 버전 미일치 시 exit 1)
#   - 본 스크립트는 cosign 이 **서명할 대상** 은 건드리지 않으며, 단지
#     설치만 한다. 서명/검증 명령은 각 워크플로 step 에서 명시적으로
#     실행한다 (cosign sign --yes / cosign verify).
#
# wiring 검증 (§13.1 Phase 3):
#   - cosign version 출력을 Actions 로그에 남긴다
#   - ${CI_SIGN_VERSION} == ${CD_VERIFY_VERSION} 을 각 파이프라인의 env
#     로 고정하여 시그니처 호환성을 유지한다 (cd.yml env.COSIGN_VERSION
#     이 단일 진실원천 — CI install 시 해당 값 그대로 전달)

set -euo pipefail

# cd.yml env.COSIGN_VERSION 과 동기화된 기본 버전. 새 pin 이 필요하면
# cd.yml 과 본 스크립트를 함께 갱신한다.
COSIGN_VERSION="${1:-v3.0.5}"
INSTALL_PREFIX="${INSTALL_PREFIX:-/usr/local/bin}"

echo "═══ Installing cosign (prefix=${INSTALL_PREFIX}, version=${COSIGN_VERSION}) ═══"

# 아키텍처 자동 감지 (GitHub-hosted runner 는 amd64, self-hosted ARM 러너
# 도입 시 확장 필요)
ARCH="$(uname -m)"
case "${ARCH}" in
    x86_64 | amd64) COSIGN_ARCH="amd64" ;;
    aarch64 | arm64) COSIGN_ARCH="arm64" ;;
    *)
        echo "❌ Unsupported architecture: ${ARCH}"
        exit 1
        ;;
esac

TARBALL_URL="https://github.com/sigstore/cosign/releases/download/${COSIGN_VERSION}/cosign-linux-${COSIGN_ARCH}"
TMP_BIN="$(mktemp -t cosign.XXXXXX)"
trap 'rm -f "${TMP_BIN}"' EXIT

curl -sSfLo "${TMP_BIN}" "${TARBALL_URL}"

# sudo 는 GitHub-hosted runner 에서는 무인증으로 쓸 수 있으나, self-hosted
# 러너에서는 사용자 수준 설치 경로(${HOME}/.local/bin) 를 선택할 수
# 있도록 INSTALL_PREFIX 를 분리했다.
if [[ -w "${INSTALL_PREFIX}" ]]; then
    install -m 0755 "${TMP_BIN}" "${INSTALL_PREFIX}/cosign"
else
    sudo install -m 0755 "${TMP_BIN}" "${INSTALL_PREFIX}/cosign"
fi

echo "═══ cosign installed — asserting version pin ═══"
# ⚠️ 반드시 ${INSTALL_PREFIX}/cosign 의 full-path 로 호출한다. bare `cosign`
# 은 PATH 룩업이므로 INSTALL_PREFIX 가 PATH 에 없거나 PATH 상 더 오래된
# cosign 이 먼저 있으면 방금 설치한 바이너리가 아닌 엉뚱한 것을 검증하게
# 되어 false success / false failure 를 낼 수 있다 (silent miss 패턴,
# development-policies.md §8, §13.1).
POST_VERSION="$("${INSTALL_PREFIX}/cosign" version 2>/dev/null | awk '/^GitVersion:/ {print $2}')"
if [[ "${POST_VERSION}" != "${COSIGN_VERSION}" ]]; then
    echo "❌ cosign version pin failed: expected=${COSIGN_VERSION}, got=${POST_VERSION:-<empty>} (binary=${INSTALL_PREFIX}/cosign)"
    exit 1
fi

echo "✅ cosign ${POST_VERSION} 고정됨 (binary=${INSTALL_PREFIX}/cosign)"
"${INSTALL_PREFIX}/cosign" version
