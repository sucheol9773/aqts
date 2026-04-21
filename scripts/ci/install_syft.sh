#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════
# Phase 3 contingency — CLI fallback for anchore/sbom-action
# ══════════════════════════════════════════════════════════════
#
# 적용 시점: 2026-08-01 에도 anchore/sbom-action 이 Node 20 런타임을
# 유지하고 있는 경우. Node 24 로 migrate 된 것이 확인되면 본 스크립트는
# 호출되지 않고 그대로 유지(장래 재활용 가능). 상세 절차:
#   agent_docs/development-policies.md §13.1 Phase 3
#
# 동작: anchore/syft 공식 install.sh 를 사용해 지정 버전을 /usr/local/bin
# 에 설치한다. install.sh 는 checksum 검증 + 아키텍처 자동 감지 + 릴리스
# GitHub API 호출을 모두 수행하므로, curl 직접 호출보다 공급망 관점에서
# 안전하다. 다만 install.sh 자체도 GitHub CDN 에 의존하므로 runner 의
# 네트워크 가드(pinned IP, 또는 네트워크 제한 러너) 가 있는 환경에서는
# 사전에 화이트리스트 등록이 필요하다.
#
# 사용:
#   bash scripts/ci/install_syft.sh            # latest
#   bash scripts/ci/install_syft.sh v1.40.0    # 특정 버전 pin
#
# 검증:
#   syft --version
#
# wiring 검증 (§13.1 Phase 3 "Wiring 검증 포인트"):
#   - syft --version 출력을 Actions 로그에 남겨 사람이 읽을 수 있게 한다
#   - 후속 step 에서 syft "${IMAGE_REF}" -o cyclonedx-json=sbom.cdx.json
#     가 성공적으로 sbom 파일을 생성하는지 확인

set -euo pipefail

SYFT_VERSION="${1:-}"  # 비워두면 install.sh 가 latest 사용
INSTALL_PREFIX="${INSTALL_PREFIX:-/usr/local/bin}"

echo "═══ Installing syft (prefix=${INSTALL_PREFIX}, version=${SYFT_VERSION:-latest}) ═══"

if [[ -n "${SYFT_VERSION}" ]]; then
    curl -sSfL https://raw.githubusercontent.com/anchore/syft/main/install.sh \
        | sh -s -- -b "${INSTALL_PREFIX}" "${SYFT_VERSION}"
else
    curl -sSfL https://raw.githubusercontent.com/anchore/syft/main/install.sh \
        | sh -s -- -b "${INSTALL_PREFIX}"
fi

echo "═══ syft installed ═══"
syft --version
