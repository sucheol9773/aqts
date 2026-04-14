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
   - `cosign-installer@v3` → `cosign sign --yes <digest>` (keyless).
   - `cosign attest --predicate sbom.cdx.json --type cyclonedx <digest>` 로 SBOM attestation.
   - `cosign verify` 셀프-체크로 sanity verify 후 GitHub step summary 에 digest/태그 출력.
3. PR 빌드는 push 없이 로컬 load 만 수행하고, non-root user 검증만 한다 (CVE 스캔/서명은 main push 한정 — Rekor 오염 방지).

## 4. CD 파이프라인 게이트 (.github/workflows/cd.yml)

배포 서버에서 SSH 를 통해 다음 순서로 실행한다.

1. `git pull origin main` — compose/config 동기화.
2. `cosign` 미설치 시 `${COSIGN_VERSION}` (현재 v2.6.3) 자동 설치.
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
  - `cosign` CLI 메이저 버전 (현재 v2 계열) — `COSIGN_VERSION` env 단일 지점에서 갱신.
  - Sigstore Fulcio root CA 변경 — `cosign initialize` 를 통한 trust root 갱신 필요 시 본 문서에 반영.

## 7. 운영자 수동 검증 절차

장애/감사 대응 시 운영자가 직접 검증하는 절차:

```bash
# 1. cosign 설치 (서버에 없을 경우)
curl -sSLo /tmp/cosign \
  "https://github.com/sigstore/cosign/releases/download/v2.6.3/cosign-linux-amd64"
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
