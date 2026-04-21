#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════
# Phase 3 contingency — CLI fallback for anchore/scan-action
# ══════════════════════════════════════════════════════════════
#
# 적용 시점: 2026-08-01 에도 anchore/scan-action 이 Node 20 런타임을
# 유지하고 있는 경우. 상세 절차: agent_docs/development-policies.md §13.1
# Phase 3.
#
# 참고: 현재 ci.yml 은 이미 "List High/Critical CVEs (grype, debug)" step
# 에서 grype CLI 를 curl 로 직접 설치해 사용 중이다 (v0.97.1 pin). 본
# 스크립트는 해당 로직을 재사용 가능한 단일 스크립트로 추출하여, 치환
# 시점에 "debug step" 과 "gate step" 이 동일한 설치 경로를 공유하도록
# 한다 — 버전 드리프트로 인한 false green/red 방지.
#
# 사용:
#   bash scripts/ci/install_grype.sh v0.97.1
#
# 기본값: v0.97.1 (ci.yml Line 362 와 동기화)
#
# wiring 검증 (§13.1 Phase 3):
#   - grype --version 출력을 Actions 로그에 남긴다
#   - grype "${IMAGE_REF}" --fail-on high -o sarif=grype.sarif 실행 시
#     SARIF 파일이 생성되고, 업로드 step (github/codeql-action/upload-sarif@v4)
#     이 이 파일을 읽는지 확인한다. 파일명은 `steps.grype.outputs.sarif`
#     를 치환하여 명시적으로 env 변수로 전달한다.

set -euo pipefail

# ci.yml 과 동기화된 기본 버전. 새 pin 이 필요하면 ci.yml 의 debug step
# 과 본 스크립트를 함께 갱신한다.
GRYPE_VERSION="${1:-v0.97.1}"
INSTALL_PREFIX="${INSTALL_PREFIX:-/usr/local/bin}"

echo "═══ Installing grype (prefix=${INSTALL_PREFIX}, version=${GRYPE_VERSION}) ═══"

curl -sSfL https://raw.githubusercontent.com/anchore/grype/main/install.sh \
    | sh -s -- -b "${INSTALL_PREFIX}" "${GRYPE_VERSION}"

echo "═══ grype installed ═══"
# ⚠️ 반드시 ${INSTALL_PREFIX}/grype 의 full-path 로 호출한다. bare `grype`
# 는 PATH 룩업이므로 INSTALL_PREFIX 가 PATH 에 없거나 PATH 상 더 오래된
# grype 가 먼저 있으면 방금 설치한 바이너리가 아닌 엉뚱한 것을 검증하게
# 되어 false success / false failure 를 낼 수 있다 (silent miss 패턴,
# development-policies.md §8, §13.1).
"${INSTALL_PREFIX}/grype" --version
