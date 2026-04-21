# 공급망 보안 정책 (Supply-Chain Security Policy)

본 문서는 AQTS 의 빌드/배포 파이프라인에서 적용되는 공급망 보안 통제(SBOM, 이미지 서명, SCA)의 단일 진실원천(SSOT)이다. 엔터프라이즈 갭 로드맵 10위(v1.30)의 산출물이며, 모든 신규 의존성/이미지/배포는 본 정책을 준수해야 한다.

## 1. 통제 개요

| 통제 | 도구 | 단계 | 차단 조건 |
| --- | --- | --- | --- |
| Python 의존성 CVE 스캔 | `pip-audit` (OSV) | CI · lint job | OSV CVE 1건 이상 (화이트리스트 제외) |
| 컨테이너 이미지 CVE 스캔 | `grype` (anchore/scan-action) | CI · docker-build job | severity ≥ high |
| SBOM 생성 | `syft` (CycloneDX JSON) | CI · docker-build job | 생성 실패 |
| 이미지 서명 | `cosign sign` keyless (Sigstore Fulcio + Rekor, OIDC) | CI · docker-build job | 서명 실패 |
| SBOM attestation | `cosign attest --type cyclonedx` | CI · docker-build job | attestation 실패 |
| 배포 시 서명 검증 | `cosign verify` (certificate-identity-regexp + OIDC issuer) | CD · deploy & rollback | 서명 검증 실패 시 배포 중단 |

## 2. 신원 및 신뢰 모델

- **서명 키**: 별도 비밀키 없음. GitHub Actions OIDC 토큰을 사용한 **Sigstore keyless** 서명. Fulcio 가 단기(10분) X.509 인증서를 발급하고, Rekor 투명성 로그에 서명을 기록한다.
- **신뢰 정책**: 배포 측은 다음 두 가지를 동시에 만족해야 통과시킨다.
  - `--certificate-identity-regexp "^https://github.com/<owner>/<repo>/"` — 우리 레포의 워크플로우만 발급한 인증서
  - `--certificate-oidc-issuer "https://token.actions.githubusercontent.com"` — GitHub Actions OIDC 발급자
- **레지스트리**: `ghcr.io/${IMAGE_NAMESPACE}/aqts-backend`. `IMAGE_NAMESPACE` 는 GitHub `repository_owner` 와 동일하게 자동 주입된다.

## 3. CI 파이프라인 게이트 (.github/workflows/ci.yml)

1. `lint` job — `pip-audit -r backend/requirements.txt --strict --vulnerability-service osv` 실행. 화이트리스트는 `backend/.pip-audit-ignore` (만료일 + 사유 + 코드 경로 명시 의무).
2. `docker-build` job — 메인 push 시:
   - `docker/metadata-action@v5` 가 `sha-XXXXXXX`, branch, semver, latest 태그 생성.
   - `docker/build-push-action@v6` 로 GHCR push (`provenance: true`, `sbom: true`).
   - `anchore/sbom-action@v0` 로 CycloneDX JSON SBOM 생성 + 90일 아티팩트 보관.
   - `anchore/scan-action@v3` (grype) `severity-cutoff: high`, SARIF 를 GitHub Security 탭에 업로드.
   - `cosign-installer@v4.1.1` (cosign v3.0.5 내장) → `cosign sign --yes <digest>` (keyless).
   - `cosign attest --predicate sbom.cdx.json --type cyclonedx <digest>` 로 SBOM attestation.
   - `cosign verify` 셀프-체크로 sanity verify 후 GitHub step summary 에 digest/태그 출력.
3. PR 빌드는 push 없이 로컬 load 만 수행하고, non-root user 검증만 한다 (CVE 스캔/서명은 main push 한정 — Rekor 오염 방지).

## 4. CD 파이프라인 게이트 (.github/workflows/cd.yml)

배포 서버에서 SSH 를 통해 다음 순서로 실행한다.

1. `git pull origin main` — compose/config 동기화.
2. `cosign` 미설치 시 `${COSIGN_VERSION}` (현재 v3.0.5) 자동 설치.
3. `cosign verify --certificate-identity-regexp "^https://github.com/${REPO_FULL}/" --certificate-oidc-issuer "https://token.actions.githubusercontent.com" "${IMAGE_REF}"` — 통과해야만 다음 단계 진행. 실패 시 즉시 종료.
4. `docker pull "${IMAGE_REF}"` 후 `EXPECTED_IMAGE_ID=$(docker image inspect "${IMAGE_REF}" --format '{{.Id}}')` 로 로컬 digest 잠금.
5. `docker compose -f docker-compose.yml up -d --force-recreate --no-deps backend scheduler` → `docker compose -f docker-compose.yml up -d`. `--force-recreate` 는 compose 의 "변경 없음" 최적화를 무력화하여 backend/scheduler 가 **원자적으로 교체**되도록 강제한다.
6. 배포 직후 `docker inspect --format '{{.Image}}'` 로 `aqts-backend`/`aqts-scheduler` 의 실행 중 image digest 가 `EXPECTED_IMAGE_ID` 와 일치하는지 어서트. 하나라도 drift 가 관측되면 즉시 exit 1 → 자동 롤백 경로 진입.
7. `Post-deploy verification` 단계는 Step 5e 와 독립적으로 backend ↔ scheduler digest 일치 여부를 재확인한다 (수동 개입/부분 재시작으로 인한 drift 재발 방지 2중 방어선).
8. 헬스체크 실패 시 자동 롤백. 롤백 경로 역시 동일한 cosign verify + `EXPECTED_IMAGE_ID` 캡처 + `--force-recreate` + digest 어서트를 강제한다. 이전 SHA 의 이미지에 대해서도 서명/digest 검증이 모두 동일하게 적용된다.

위 4–7 항목은 2026-04-08 POST_MARKET 회귀 (backend ↔ scheduler image drift 로 인해 구버전 scheduler 가 `a93fd8e` 의 멱등성/안전망 가드 없이 동작한 사건) 의 재발 방지 조치이다. 정적 wiring 검증은 `backend/tests/test_cd_atomic_deploy.py` 가 `cd.yml` 을 파싱하여 강제한다.

`docker-compose.yml` 의 `backend`/`scheduler` 서비스는 `image: ghcr.io/${IMAGE_NAMESPACE:?...}/aqts-backend:${IMAGE_TAG:-latest}` 만 참조한다. `build:` 블록은 `docker-compose.override.yml` (개발용)에만 존재한다.

## 5. 화이트리스트 / 예외 정책

- 모든 화이트리스트는 만료일을 가져야 하며 (`backend/.pip-audit-ignore`), 만료일 이후에는 자동으로 무효화되어 CI 가 다시 차단한다. 영구 예외 금지.
- 사유는 "운영상 영향 없음" 같은 추상적 표현 금지 — 구체적 코드 경로/우회 방법/후속 조치 일정 명시.
- 신규 항목 추가 PR 은 PR 본문에 근거 + 만료 후 액션 플랜 기재.

## 6. 키 / 인증 회전

- keyless 서명은 비밀키가 없으므로 회전 대상이 아니다. 대신 다음을 모니터링한다.
  - GitHub OIDC 발급자(`token.actions.githubusercontent.com`) 변경 — 변경 시 verify 정책 업데이트 필요.
  - `cosign` CLI 메이저 버전 (현재 v3 계열) — CI 측은 `sigstore/cosign-installer` 핀 지점(`v4.1.1`), CD 측은 `COSIGN_VERSION` env 단일 지점에서 갱신한다. 두 경로를 항상 동일 메이저 라인으로 맞춘다.
  - Sigstore Fulcio root CA 변경 — `cosign initialize` 를 통한 trust root 갱신 필요 시 본 문서에 반영.

## 7. 운영자 수동 검증 절차

장애/감사 대응 시 운영자가 직접 검증하는 절차:

```bash
# 1. cosign 설치 (서버에 없을 경우)
curl -sSLo /tmp/cosign \
  "https://github.com/sigstore/cosign/releases/download/v3.0.5/cosign-linux-amd64"
sudo install -m 0755 /tmp/cosign /usr/local/bin/cosign

# 2. 검증 (현재 main 의 short SHA 를 사용)
SHORT_SHA=$(git rev-parse --short=7 HEAD)
IMAGE_REF="ghcr.io/<owner>/aqts-backend:sha-${SHORT_SHA}"

cosign verify \
  --certificate-identity-regexp "^https://github.com/<owner>/<repo>/" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
  "${IMAGE_REF}"

# 3. SBOM attestation 조회
cosign download attestation "${IMAGE_REF}" \
  | jq -r '.payload' | base64 -d | jq '.predicate' > sbom.cdx.json
```

## 8. 회고 / 결정 근거

10위 작업 시 두 갈래 옵션이 있었다.

- (A) **정공법** — GHCR 전환 + cosign verify 를 배포 가드로 강제. 단일 진실원천 1개, 검증 누락 불가.
- (B) SBOM/SCA 만 추가하고 서버 빌드 유지. 변경 폭 작지만 "서명한 이미지가 실제 배포되는지" 보장 불가.

A 를 선택했다. 이유: SBOM/서명을 만들어도 실제로 배포되는 산출물이 다르면 통제가 형식적이 된다. 빌드/검증/실행이 동일 digest 를 따라가는 단일 흐름이 엔터프라이즈 통제의 본질이다.

## 8. 회고 — 2026-04-09 CVE-2026-31790 (apt 레이어 cache 재사용)

### 증상
CI `Grype Scan backend image (fail on High/Critical)` 단계가 High 2건으로 실패:

```
High  libssl3  3.0.18-1~deb12u2  3.0.19-1~deb12u2  CVE-2026-31790
High  openssl  3.0.18-1~deb12u2  3.0.19-1~deb12u2  CVE-2026-31790
```

### 원인
`backend/Dockerfile` 의 builder/runtime stage 양쪽에 이미 `apt-get update && apt-get upgrade -y` 가 있었음에도 불구하고, `.github/workflows/ci.yml` 의 `docker/build-push-action@v6` 가 `cache-from: type=gha` 로 **apt upgrade RUN 레이어를 재사용**했다. 그 결과 deb12u3 보안 피드가 흡수되지 않고 구 버전(3.0.18-1~deb12u2) 이 그대로 포함됐다.

핵심 오독: "Dockerfile 에 `apt-get upgrade` 를 넣었으므로 OS 패치는 흡수된다" 는 가정은 **레이어 캐시가 없을 때만** 성립한다. buildx gha cache 를 쓰면 RUN 문자열이 동일한 이상 레이어가 재사용되고, debian 보안 피드 업데이트는 반영되지 않는다. 이는 supply-chain 도메인의 Wiring Rule — "정의했다(Dockerfile에 upgrade 가 있다) ≠ 적용됐다(빌드 시점에 실제로 apt 가 돌았다)" — 와 같은 구조다.

### Fix
apt 레이어에만 **일(day)-단위 cache-bust build-arg** 를 주입한다. pip/torch 등 상위 레이어 캐시는 유지되어 빌드 시간 impact 가 거의 없다.

- `backend/Dockerfile`: builder/runtime stage 양쪽에 `ARG APT_UPGRADE_DATE=unknown` 선언 + apt RUN 블록 첫 줄에 `echo "apt-upgrade-date=${APT_UPGRADE_DATE}"` 추가. ARG 참조가 RUN 문자열에 포함되어야 레이어 해시가 날짜별로 달라진다.
- `.github/workflows/ci.yml`: `Compute apt upgrade date (cache-bust)` step 을 추가해 `date -u +%Y-%m-%d` 출력을 `steps.apt_date.outputs.today` 로 노출하고, `docker/build-push-action` 의 `build-args:` 에 `APT_UPGRADE_DATE=${{ steps.apt_date.outputs.today }}` 로 전달한다.
- `backend/tests/test_dockerfile_apt_cachebust.py`: 두 stage 의 ARG 선언, apt RUN 의 변수 참조, ci.yml 의 build-arg 주입을 정적으로 강제하는 회귀 테스트 6건 추가.

### 운영 원칙 업데이트
- **새 base image / Dockerfile 변경 시** 체크리스트에 다음을 추가한다: "apt 레이어 cache-bust 메커니즘이 여전히 작동하는지 확인" (ARG 이름 변경, RUN 문자열에서 참조 제거 등은 회귀). 회귀 테스트(`test_dockerfile_apt_cachebust.py`) 가 이를 정적 검사로 잡는다.
- CVE 게이트 실패 → 첫 반응은 **화이트리스트 확장이 아니라 fix 버전 흡수 경로 점검**이다. 본 건은 fix 버전(`3.0.19-1~deb12u2`) 이 이미 upstream 에 있었고 단순히 캐시 재사용으로 인해 흡수되지 못한 케이스였다. 화이트리스트는 fix 가 없거나 fix 일정이 잡힌 경우의 마지막 수단이어야 한다.

## 9. 회고 — 2026-04-16 cosign keyless OIDC 파싱 비호환 (v2.4→v2.6→v3 승격)

### 증상
`Sign image with cosign (keyless)` 스텝이 Fulcio OIDC 토큰 파싱 단계에서 실패:

```
fetching ambient OIDC credentials:
invalid character 'u' looking for beginning of value
```

JSON 디코더가 첫 바이트 `'u'` 를 만났다는 신호다. 이는 OIDC 엔드포인트 응답이 `unauthorized` 같은 평문 혹은 HTML 오류 페이지이거나, 파서 경로 내부에서 non-JSON 바이트를 JSON 으로 해석하려는 경우에 관측된다.

### 경위 타임라인
- 2026-04-14: 초기 도입 시 `cosign-installer@v3.7.0` (cosign v2.4.0 내장) 고정. CI 에서 첫 실패 발생.
- 2026-04-15 (`3c0b850`): `cosign-installer@v3.10.1` (cosign v2.6.1) 으로 점프. 동일 스텝이 한두 번은 통과한 것으로 보이나 지속적으로 재현.
- 2026-04-16: 동일 증상 재발. 조사 결과 `cosign-installer@v3` 시리즈는 cosign v2.x 만 탑재하며, OIDC 파서 경로 수정은 v3 라인에만 포함됨. `cosign-installer@v4.1.1` (cosign v3.0.5 내장) 이 업스트림 stable.

### Fix
- `.github/workflows/ci.yml`: `sigstore/cosign-installer@v3.10.1` → `@v4.1.1`. sign 단계 직전에 **관찰용 진단 스텝** 추가 — `cosign version`, `ACTIONS_ID_TOKEN_REQUEST_URL`/`_TOKEN` 존재 여부(값은 redact), `GITHUB_*` 컨텍스트. 재발 시 원인이 cosign CLI 인지 OIDC ambient 환경인지 분리 관측 가능.
- `.github/workflows/cd.yml`: `COSIGN_VERSION: v2.6.3` → `v3.0.5`. sign/verify 쌍의 메이저 라인 동기화. CD 는 verify 만 사용하므로 OIDC 는 무관하지만, 이후 attestation 포맷/verify 옵션이 메이저 간 달라질 수 있어 선제적 동기화.
- `docs/security/supply-chain-policy.md`: cosign-installer 버전 핀, `COSIGN_VERSION` 기본값, 운영자 수동 검증 절차의 `curl` URL 갱신.

### 운영 원칙 업데이트
- **cosign CLI 메이저 버전은 CI(installer 핀)와 CD(`COSIGN_VERSION` env)가 항상 동일 메이저 라인** (현재 v3) 을 유지한다. 한 쪽만 승격하면 attestation 포맷/verify 옵션 차이로 인한 서명 검증 실패가 발생할 수 있다.
- **OIDC ambient 장애 vs CLI 파싱 장애 구분**: 재발 시 관찰 스텝 출력을 먼저 확인한다. 환경변수가 `UNSET` 이면 `permissions: id-token: write` 가 job 레벨이 아닌 workflow 레벨에만 있는지 점검. 환경변수가 `set` 인데 파싱 실패면 CLI 버전 승격 검토.
- **`cosign-installer` 메이저 승격은 CLI 메이저 승격을 수반**한다. v3 → v4 는 cosign v2 → v3 을 의미한다. 단순 점프가 아니므로 릴리스 노트에서 verify 옵션 / attestation 포맷 차이 (예: `--type cyclonedx` 경로, certificate identity regexp 처리) 를 확인한 뒤 승격한다.
- 일반화된 원칙: "supply-chain 도구 체인의 관찰 가능성은 파이프라인의 1 급 자산이다." CI 의 진단 스텝은 장애 첫 발생 시점에 진단 정보를 노출하여 추측성 롤백 사이클을 차단한다 — RBAC Wiring Rule / 알림 파이프라인 Wiring Rule 의 공급망 도메인 확장이다.

### 2차 회귀 — 2026-04-16 CD `cosign version` presence-only 가드 (Silent miss)

### 증상
위 CI 수정이 머지된 직후 CD `Deploy to server → Step 3: Verify image signature` 가 다음 에러로 실패:

```
GitVersion: v2.4.0
...
Error: no signatures found
main.go:69: error during command execution: no signatures found
```

CI 는 cosign v3.0.5 로 이미지를 서명했지만, 서버의 cosign 은 여전히 v2.4.0 이었다.

### 원인
CD `cd.yml` Step 2 의 설치 가드:

```bash
if ! command -v cosign >/dev/null 2>&1; then
  echo "cosign 미설치 → 설치 진행 (${COSIGN_VERSION})"
  curl -sSLo /tmp/cosign ...
fi
```

서버에 구버전(v2.4.0) 이 이미 존재 → `command -v cosign` 이 성공 → **설치 블록 통째로 스킵**. `COSIGN_VERSION` env 를 v2.6.3 → v3.0.5 로 바꿨지만 이 값이 참조되는 경로로 진입 자체를 하지 않았다.

CLAUDE.md "Silence Error 의심 원칙 — 조건 분기 우회" 의 정확한 사례. 코드 변경이 "건드리지 않은 분기"로 빠지면서 에러가 나지 않고 구버전이 silently 재사용됐다. 외부 관찰로는 Step 2 `✓` 로 성공.

### Fix
`cd.yml` deploy + rollback 양 경로에 동일 로직:

```bash
INSTALLED_COSIGN_VERSION=""
if command -v cosign >/dev/null 2>&1; then
  INSTALLED_COSIGN_VERSION="$(cosign version 2>/dev/null | awk '/^GitVersion:/ {print $2}')"
fi
if [ "${INSTALLED_COSIGN_VERSION}" != "${COSIGN_VERSION}" ]; then
  # curl + install
fi
cosign version
POST_COSIGN_VERSION="$(cosign version 2>/dev/null | awk '/^GitVersion:/ {print $2}')"
if [ "${POST_COSIGN_VERSION}" != "${COSIGN_VERSION}" ]; then
  echo "❌ cosign 버전 고정 실패: expected=${COSIGN_VERSION}, got=${POST_COSIGN_VERSION:-<empty>}"
  exit 1
fi
```

핵심 변경:
- presence 대신 **실제 버전 파싱 + 비교**
- 설치 후 **재파싱 + 어서트** (설치 자체가 실패한 경우 loud failure)
- rollback SSH env 에 `COSIGN_VERSION` 추가 전달 (deploy Step 2 실패 시에도 rollback 이 올바른 버전을 강제)

### 회귀 방지
`backend/tests/test_cd_atomic_deploy.py::TestCosignVersionPinning` 에 6건의 정적 회귀 테스트 추가:
- deploy/rollback 각각의 버전 비교 분기
- deploy/rollback 각각의 post-install 어서트 + exit 1
- rollback 스텝 env 의 `COSIGN_VERSION` 전달
- **presence-only 회귀 방어**: `command -v cosign` 이 있으면 반드시 `INSTALLED_COSIGN_VERSION` 비교가 함께 있어야 한다는 구조 어서트

### 교훈 (운영 원칙 확장)
- **환경변수 값을 바꾸는 변경은 "해당 값이 참조되는 경로로 들어가는지" 를 먼저 관찰한다**. env 수정이 silently 무시되는 경로가 있는지 grep 으로 전수 확인. 본 건에서는 `COSIGN_VERSION` 이 `if ! command -v` 블록 **안에서만** 참조되고, 그 블록으로 들어가는 조건이 server 상태에 의존했다.
- **presence check + version pin 의 혼용은 드리프트를 재생산한다**. 버전 pin 이 존재하는 파이프라인에서는 presence-only 가드를 금지한다. 설치 블록은 반드시 "현재 버전 파싱 → 비교 → 불일치 시 재설치 → post-install 어서트" 4 단계로 구성한다.
- **"어제 고친 버그가 오늘 같은 증상으로 재발"하면 분기 경로를 의심한다**. 외부 증상은 동일해도 원인 지점이 다를 수 있다 — 본 2차 회귀의 원인은 1차(CI)와 완전히 다른 파일(`cd.yml`)의 완전히 다른 로직이었다.

## 10. 회고 — 2026-04-22 `python-dotenv` GHSA-mf9w-mj56-hr94 (CVE-2026-28684)

### 증상

2026-04-21 늦은 시점부터 CI 의 `Python 의존성 CVE 스캔 (pip-audit)` Step 이 다음 advisory 를 차단:

```
Found 1 known vulnerability, ignored 3 in 1 package
Name            Version  ID                     Fix Versions
python-dotenv   1.0.1    GHSA-mf9w-mj56-hr94    1.2.2
```

본 Step 은 `chore/python-version-align` (PR #13) 이 포함하는 변경과 무관하게 차단되었다 — `python-dotenv` 는 PR #13 범위에 없다.

### 원인

- Advisory published 2026-04-19, updated 2026-04-21 14:38 UTC. pip-audit 의 vulndb 가 2026-04-21 오후 동기화 직후부터 탐지 시작.
- CVSS 6.6 / Moderate. CWE-59 + CWE-61 (symlink following).
- Attack vector: `set_key()` / `unset_key()` 경로에서 `shutil.copy2()` / `shutil.move()` 이 symlink 를 따라가며 cross-device rename fallback 시 symlink target 으로 리다이렉트 → 공격자가 `.env` 경로에 사전 배치한 symlink 로 임의 파일 덮어쓰기 가능.
- Fix: upstream 1.2.2 (https://github.com/theskumar/python-dotenv/commit/790c5c02991100aa1bf41ee5330aca75edc51311).

### Reachability 평가

AQTS 코드베이스 grep 결과:

- `load_dotenv()` 사용처 4건 — `scripts/run_backtest.py:34`, `scripts/run_walk_forward.py:28`, `scripts/run_hyperopt.py:39`, `scripts/backfill_market_data.py:194`
- `set_key` / `unset_key` 사용처 **0건**

취약 API (쓰기 경로) 는 사용되지 않으므로 운영상 exploit 경로는 **현재 없음**. 그러나 향후 동적 `.env` 기록 기능이 추가될 가능성 + advisory 가 존재하는 의존성을 그대로 핀하는 위험 (supply-chain hygiene) 을 고려하여 **ignore 가 아닌 업그레이드** 를 선택했다.

### Fix

- `backend/requirements.txt` 라인 91: `python-dotenv==1.0.1` → `python-dotenv==1.2.2`
- 1.0.1 → 1.2.2 CHANGELOG 주요 변경 (load 경로 영향 0):
  - 1.1.0: 기본 인코딩 `None` → `"utf-8"` (안전성 개선, 회귀 없음)
  - 1.1.1: `dotenv_values` 반환 타입 `OrderedDict` → `dict` (Python 3.7+ dict 순서 보존 → 사실상 호환)
  - 1.2.0: `Path` 타입 인자 공식 지원 (기존 str 경로 API 유지)
  - 1.2.1 / 1.2.2: 버그픽스 + 본 CVE 패치
- `.pip-audit-ignore` 수정 없음 — 업그레이드로 해소했으므로 예외 등록 불필요.

### 운영 원칙 업데이트

- **"reachability 가 없어도 업그레이드 가능하면 업그레이드"**. Fix 버전이 CHANGELOG 상 호환되는 범위(minor + patch)에 있고 breaking change 가 없다면, 공급망 hygiene 을 위해 reachability 논리로 ignore 를 추가하기보다 업그레이드로 해소한다. ignore 는 (a) fix 가 아직 없거나 (b) fix 에 수용 불가한 breaking change 가 있을 때만.
- **advisory publish timestamp 와 CI 차단 시점의 lag 를 기록**. pip-audit 의 vulndb 동기화 주기가 있으므로, CI 가 "갑자기" 실패하기 시작한 경우 해당 advisory 의 published/updated 시각을 확인하여 "내가 한 변경이 원인인가 vs 외부 DB 업데이트가 원인인가" 를 먼저 분리한다.
- **PR 스코프 규율 우선**. CVE 차단은 `chore/python-version-align` (PR #13) CI 에서 먼저 관측되었지만, CVE 픽스는 py311 정합성과 완전히 orthogonal 한 공급망 이슈이므로 별도 브랜치 (`chore/bump-python-dotenv-1.2.2`) 로 분리 처리했다 (CLAUDE.md §7 "bug fix 커밋에 무관한 '이왕 고치는 김에' 변경을 끼워넣지 않는다"). 한 PR 에 두 주제를 섞으면 나중에 `git blame` / revert 단위가 오염되고, 본 CVE 픽스가 `chore/python-version-align` 이외 다른 브랜치로 확산되는 시점도 지연된다.
