#!/usr/bin/env bash
# ──────────────────────────────────────────────
# set_admin_password.sh
# .env 파일에 ADMIN_PASSWORD 를 설정/업데이트하는 스크립트
#
# 사용법:
#   ./scripts/set_admin_password.sh
#   ./scripts/set_admin_password.sh "my_password"
# ──────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$REPO_ROOT/.env"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "❌ .env 파일을 찾을 수 없습니다: $ENV_FILE"
    exit 1
fi

# 인자로 비밀번호를 받거나, 프롬프트로 입력받기
if [[ $# -ge 1 ]]; then
    NEW_PASSWORD="$1"
else
    read -rsp "새 ADMIN_PASSWORD 입력: " NEW_PASSWORD
    echo
    if [[ -z "$NEW_PASSWORD" ]]; then
        echo "❌ 비밀번호가 비어있습니다."
        exit 1
    fi
fi

# 기존 ADMIN_PASSWORD 라인이 있으면 교체, 없으면 추가
if grep -q "^ADMIN_PASSWORD=" "$ENV_FILE"; then
    # 기존 값 교체 (sed 구분자로 | 사용 — 비밀번호에 / 가 있을 수 있으므로)
    sed -i "s|^ADMIN_PASSWORD=.*|ADMIN_PASSWORD=${NEW_PASSWORD}|" "$ENV_FILE"
    echo "✅ ADMIN_PASSWORD 업데이트 완료"
else
    # 파일 끝에 추가
    echo "" >> "$ENV_FILE"
    echo "# Admin 계정 비밀번호 (API 인증용)" >> "$ENV_FILE"
    echo "ADMIN_PASSWORD=${NEW_PASSWORD}" >> "$ENV_FILE"
    echo "✅ ADMIN_PASSWORD 추가 완료"
fi

# 검증
STORED=$(grep -oP 'ADMIN_PASSWORD=\K.*' "$ENV_FILE")
echo "   저장된 값: ${STORED:0:3}***${STORED: -1} (${#STORED}자)"
