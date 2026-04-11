# AQTS 엔터프라이즈 갭 로드맵

본 문서는 2026-04-06 작성된 엔터프라이즈 갭 분석을 단일 진실원천(SSOT)으로 보존하고, 진행 상황·우선순위·다음 작업 후보를 한 곳에서 추적하기 위한 로드맵이다. 컨텍스트 손실 시 이 문서만으로 다음 작업을 결정할 수 있어야 한다.

## 0) 평가 범위/기준

근거 산출물:
- 코드: `backend/`, `scripts/`, `monitoring/`, `.github/workflows/`
- 문서: `docs/PRD.md`, `docs/FEATURE_STATUS.md`, `docs/operations/*`, `README.md`

평가 12개 축: 제품/거버넌스, 아키텍처/확장성, 신뢰성/가용성, 보안/인증/권한, 데이터/스키마/계보, 모델 리스크/ML-Ops, 트레이딩 운영 통제, 관측성/운영 자동화, SDLC/품질 게이트, 배포/런타임 인프라, 규제/감사/컴플라이언스, 문서 정합성/운영 실행가능성.

---

## 1) 현재 강점 (기반은 양호)

1. 데이터수집→팩터→시그널→앙상블→주문→감사→모니터링까지 계층화된 모듈 구조.
2. CI에 lint/smoke/full test 분리, FEATURE_STATUS의 Tested 중심 관리.
3. 인시던트 런북, 롤백 계획, 릴리스 게이트 문서 체계 존재.
4. 게이트 기반 파이프라인, 손실 제한, circuit breaker, dry-run, OOS/민감도 분석 모듈 존재.

요약: "개인/소규모 운영형 퀀트 시스템"으로는 매우 높은 완성도.

---

## 2) 핵심 갭 요약 — 구조적 갭 7개

1. 멀티테넌시/멀티유저 부재 (PRD가 단일 사용자 자가매매 명시).
2. 인증·권한 모델 단순화 (단일 비밀번호 + HS256 JWT).
3. 인프라 HA 설계 부족 (Compose 단일 스택, Active-Active/DR 미비).
4. 스키마 변경관리 체계 부족 (init SQL 중심).
5. 운영 보안 하드닝 미흡 (SSH `StrictHostKeyChecking no` 등).
6. 알림/SRE 체계 미완성 (수집은 있으나 Alertmanager/rule/on-call 약함).
7. 문서-현실 불일치 (테스트 수치/상태 문서 간 상충).

---

## 3) 상세 갭 분석

### 3.1 제품/거버넌스
- 관찰: PRD가 Phase 1을 단일 사용자 자가매매 도구로 정의. FEATURE_STATUS의 Production-ready 0건.
- 부족: B2B/B2C 운영 거버넌스(승인 워크플로, 권한 분리, 고객별 정책) 미존재.
- 영향: 기관 운영 승인(내부통제/책임소재/권한분리) 통과 곤란.

### 3.2 아키텍처/확장성
- 관찰: Docker Compose 단일 배포, uvicorn workers=1, scheduler/KIS 클라이언트 동일 프로세스 초기화.
- 부족: 서비스 분리·독립 스케일링, 메시지 큐 기반 비동기 워크플로, cell-based 장애 격리, region/zone 분산 부재.
- 영향: 거래량/사용자 증가 시 병목, 장애 전파 가능성 큼.

### 3.3 신뢰성/가용성
- 관찰: SLA/RTO/RPO 문서 존재. health endpoint가 degraded여도 200 가능. CD는 단일 SSH 배포.
- 부족: RPO=0 주장 대비 WAL shipping/PITR/다중 AZ 자동화 증거 부족, B/G·Canary·자동 롤포워드, Chaos/DR drill 정례화 부족.
- 영향: 장애 복구가 문서 중심 수동 대응에 의존, 복구시간 편차 큼.

### 3.4 보안/인증/권한
- 관찰: 단일 사용자 패스워드 → JWT(HS256) 발급. bcrypt 미저장 시 평문 비교 허용. CD `StrictHostKeyChecking no`.
- 부족: RBAC/ABAC, SSO(OIDC/SAML), MFA, 세션 강제 만료/디바이스 정책, JWT key rotation(kid), jti revocation, audience/issuer 강제 부재. SSH 호스트 키 비검증. 비밀관리 모듈 존재하나 Vault/KMS 강제 경로 불명.
- 영향: 운영자 계정 탈취/오남용 취약, 감사 시 권한분리 미흡 지적 가능.

### 3.5 데이터/스키마/계보
- 관찰: `scripts/init_db.sql` 중심, alembic은 requirements에만, 핵심 엔티티 JSONB 유연 스키마.
- 부족: 선언적 마이그레이션(승인/롤백/검증) 표준화, 데이터 계보/영향도 분석 자동화, 데이터 품질 SLO 대시보드/알림 부재.
- 영향: 스키마 변경 회귀 위험, 운영 중 데이터 의미 붕괴 위험.

### 3.6 모델 리스크/ML-Ops
- 관찰: OOS, 민감도, 승격 체크리스트 존재. LLM 모델명/버전 하드코딩.
- 부족: 모델 레지스트리(승인상태/lineage/artifact immutability/signing) 표준 부족, 실시간 드리프트 자동 롤백/디그레이드, 오프라인/온라인 feature parity 검증 미흡.
- 영향: 드리프트 발생 시 사람 의존 대응, 손실 확대 가능.

### 3.7 트레이딩 운영 통제
- 관찰: 손실한도, 중단/재개, emergency monitor 구현. 운영 체크리스트 존재.
- 부족: 4-eyes principle(주문 이중결재), 전략 변경 승인 분리, 긴급모드 권한분리, pre/post-trade 통제 로그의 immutable/WORM 저장소, 브로커/시장 failover routing 다중화 부족.
- 영향: 운영자 실수/오판에 대한 조직적 완충장치 약함.

### 3.8 관측성/운영 자동화(SRE)
- 관찰: Prometheus scrape, Grafana 대시보드 준비. 인시던트 런북 문서화.
- 부족: Alert rule/Alertmanager/paging/on-call 코드 명시 약함, SLI/SLO error budget 운영(릴리즈 게이트 연동), 분산 추적/request correlation 표준화 약함.
- 영향: "보는 것"과 "자동 대응" 사이 간극 큼.

### 3.9 SDLC/품질 게이트
- 관찰: CI에 lint/smoke/full-test/build, 문서 정합성 검사 스크립트.
- 부족: 브랜치 보호(필수 리뷰/코드오너/서명 커밋), SAST/DAST/SCA/license scan/SBOM/이미지 서명(cosign), 성능 회귀 벤치마크 게이트 부재.
- 영향: 공급망·보안·성능 측면 엔터프라이즈 감사 대응력 낮음.

### 3.10 배포/런타임 인프라
- 관찰: GitHub Actions → 원격 SSH → compose 빌드/재기동. 롤백은 reset 기반.
- 부족: IaC(Terraform), 이미지 불변배포, 아티팩트 프로모션(dev→stg→prod), 환경별 구성 분리(Secrets/Config/Policy as code), SSH 호스트 키 비검증.
- 영향: 재현성·감사 추적성 낮음, 인적 실수/보안 리스크 상승.

### 3.11 규제/감사/컴플라이언스
- 관찰: 감사무결성/보존/PII 마스킹/리포트 코드화.
- 부족: 법규 매핑(traceability matrix)과 외부감사 패키지, 키 수명주기(HSM/KMS), 접근 통제 감사(log of log access), 데이터 주권/국외 이전/위탁 프로세스 연결 약함.
- 영향: 외부 규제기관/감사법인 요구수준 충족 불확실.

### 3.12 문서 정합성/운영 실행가능성
- 관찰: PRD/FEATURE_STATUS/release-gates 수치 시점별 혼재. ~~`CORS_ORIGINS` vs `CORS_ALLOWED_ORIGINS` 키 명칭 불일치~~ → `.env.example` 정정 완료 (2026-04-11).
- 부족: SSOT 지표 체계 약함, 문서 간 버전 동기화 엄격성 부족.
- 영향: 장애/배포 시 커뮤니케이션 비용 증가, 의사결정 지연.

---

## 4) 우선순위 로드맵 (ROI 순)

### P0 (0~4주) — "사고 예방"

| # | 항목 | 상태 | 릴리즈 |
| --- | --- | --- | --- |
| 1 | Prometheus Alerting 체계 (rule + Alertmanager + 텔레그램) | ✅ 완료 | v1.23 |
| 2 | DB 변경관리 표준화 (Alembic 정식 도입, init_db.sql 베이스라인) | ✅ 완료 | v1.23 |
| 3 | 배포 보안 하드닝 (SSH known_hosts 검증, `StrictHostKeyChecking` 활성) | ✅ 완료 | v1.24 |
| 4 | 인증/권한 고도화 — JWT key rotation(kid), jti revocation, bcrypt 전용, 평문 fallback 제거 | ✅ 완료 | v1.24 |
| 4-α | 인증/권한 고도화 — 사용자 계정+RBAC, MFA, OIDC SSO | ⏳ 잔여 | — |

### P1 (1~2개월) — "운영 안정화"

| # | 항목 | 상태 | 릴리즈 |
| --- | --- | --- | --- |
| 5 | DR/백업 자동화 — pg_dump/mongodump cron + GCS 업로드 + 복원 스크립트 + PITR(WAL archiving) | ✅ 완료 | v1.25 |
| 6 | 런타임 분리 — 스케줄러 컨테이너 분리(SCHEDULER_ENABLED), 장애 격리 | ✅ 완료 | v1.25 |
| 7 | 관측성 고도화 — OpenTelemetry tracing(FastAPI/SQLAlchemy/httpx/Redis) + OTel Collector + Jaeger | ✅ 완료 | v1.26 |
| 7-α | 환경변수 bool 표기 표준화 (보너스, 7위 직후 추가) | ✅ 완료 | v1.27 |
| 5-α | DR 복구훈련 정례화, RTO/RPO 실측 대시보드 | ⏳ 잔여 | — |

### P2 (2~4개월) — "엔터프라이즈 적합성"

다음 작업 후보 (8위부터). ROI(영향도 × 긴급도 ÷ 비용) 기준 정렬.

| 순위 | 항목 | 근거 (3장 절) | 예상 산출물 |
| --- | --- | --- | --- |
| **8** ✅ | 문서 SSOT 자동화 — `scripts/gen_status.py` (FEATURE_STATUS/README/release-gates 테스트 수치 AST 기반 자동 갱신, --check/--update, changelog 보존, Doc Sync CI 통합) | 3.12 | v1.28 |
| **9** ✅ | RBAC + 사용자 계정 모델 — `users`/`roles` 모델 + Alembic, viewer/operator/admin 분리, TOTP MFA, `require_*` 의존성 가드, 정적 검사 + 통합 테스트 wiring 검증 | 3.4 | v1.29 |
| **10** ✅ | SBOM + 이미지 서명 + SCA — `pip-audit`(OSV), `grype`(컨테이너 CVE), `syft`(CycloneDX SBOM), `cosign` keyless OIDC 서명/attestation, GHCR 전환, CD `cosign verify` 강제 게이트 | 3.9 | v1.30 |
| **11** | 4-eyes 주문 승인 + 불변 감사 저장소 — 임계 금액 이상 주문/전략 변경에 이중결재, append-only 로그 | 3.7, 3.11 | `OrderApproval` 모델, WORM 어댑터(GCS object versioning), 테스트 |
| **12** | IaC(Terraform) + 환경 분리 — dev/stg/prod 분리, secrets/config as code, 아티팩트 프로모션 | 3.10 | `infra/terraform/`, GitHub Actions OIDC, 환경별 변수 |

후순위 (P2 이후 또는 별도 트랙):
- 데이터 품질 SLO 대시보드 (3.5)
- 모델 드리프트 자동 롤백/디그레이드 (3.6)
- 멀티 브로커 failover routing (3.7)
- SLI/SLO error budget 릴리즈 게이트 연동 (3.8)
- 성능 회귀 벤치마크 게이트 (3.9)
- Chaos engineering / DR drill 정례화 (3.3)
- 멀티테넌시/멀티유저 지원 (3.1)

---

## 5) 결론

AQTS는 기능 구현/테스트 폭 측면에서 개인·소규모 실운영 시스템으로 매우 우수하다. 기업단위 운영의 핵심은 "기능 존재"가 아니라 "통제/보안/가용성/거버넌스의 강제력"이며, 현재는 **기능 성숙도 高 / 엔터프라이즈 강제력 中 이하**.

P0~P1(1~7위)이 모두 완료되어 사고 예방·운영 안정화 1차 라운드는 끝났다. 다음 분기 목표는 **8위 문서 SSOT 자동화 → 9위 RBAC → 10위 SBOM/이미지 서명** 순으로 진행하는 것이 ROI가 가장 높다.

---

## 부록 A. 이전 릴리즈 매핑

| 릴리즈 | 항목 | 본 로드맵 매핑 |
| --- | --- | --- |
| v1.22 | 카나리 배포 인프라 (nginx split_clients, canary_deploy.sh) | P0 사전작업 |
| v1.23 | Prometheus Alerting + Alembic 마이그레이션 | P0 #1, #2 |
| v1.24 | JWT 강화 + SSH 하드닝 | P0 #3, #4 |
| v1.25 | DB 백업/PITR + 스케줄러 분리 | P1 #5, #6 |
| v1.26 | OpenTelemetry 분산 추적 | P1 #7 |
| v1.27 | 환경변수 bool 표기 표준화 | P1 #7-α (보너스) |
| v1.28 | 문서 SSOT 자동화 (gen_status.py) | P2 #8 |
| v1.29 | RBAC + 사용자 계정 + TOTP MFA + 라우트 가드 wiring 검증 | P2 #9 |
| v1.30 | 공급망 보안 — SBOM/cosign keyless 서명/grype/pip-audit + GHCR `cosign verify` 배포 게이트 | P2 #10 |
