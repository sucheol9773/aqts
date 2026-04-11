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
### P3 (4~8개월) — "운영 성숙도 + 프로덕션 전환 준비"

2026-04-12 갭 분석 및 전문가 리뷰를 기반으로, P2 이후 잔여 갭을 9개 실행 블록으로 구체화했다. 각 블록은 독립적으로 머지 가능한 단위이며, 실행 순서는 현재 상태(DEMO, 단일 GCP 서버, 단독 운영자)를 전제로 리스크 대비 효과가 큰 순서로 배치한다.

#### 권장 실행 순서

| 순서 | 블록 | 근거 |
|------|------|------|
| 1 | 블록 A: Backend↔Scheduler 분리 강화 | 가장 작은 변경으로 가장 큰 안정성 향상. `SCHEDULER_ENABLED` 이미 존재하므로 compose 분리만 실행 |
| 2 | 블록 B: TLS + 네트워크 접근통제 | 외부 접근 차단이 선행되어야 나머지 보안 작업에 의미가 생김 |
| 3 | 블록 C: DR 수용 기준 정의 + 블록 D: DR 드릴 자동화 | 복구 불능 상태에서 다른 개선을 해봐야 서버 사고 한 번이면 전부 소실 |
| 4 | 블록 E: 시크릿 수명주기 관리 | TLS 적용 후 키 회전 체계를 잡아야 의미가 있음 |
| 5 | 블록 F: Terraform IaC v1 | 여기까지 오면 "서버를 날려도 재현 가능" 상태가 됨 |
| 6 | 블록 G: Canary/Blue-Green 배포 연결 + 블록 H: 장애 격리 토폴로지 문서화 | IaC 위에서 배포 전략을 설계해야 일관성 유지 |
| 7 | 블록 I: 4-eyes + 감사 불변성 | 멀티유저 + 실자금 투입 시점에 맞춰 구현 |

---

#### 블록 A: Backend↔Scheduler 프로세스 분리 및 장애 격리

**근거**: §3.2 아키텍처/확장성, §3.3 신뢰성/가용성

`SCHEDULER_ENABLED` 플래그가 이미 존재하므로 이를 실제 운영 토폴로지로 승격한다.

**실행 항목**:

1. **프로세스/컨테이너 분리**
   - compose에서 `backend-api`와 `backend-scheduler`를 별도 서비스로 분리
   - scheduler 전용 env(`SCHEDULER_ENABLED=true`, API는 `false`) 적용
   - 각 서비스 healthcheck 독립화

2. **리소스 격리**
   - API/스케줄러 각각 CPU·메모리 제한 설정
   - OOM 시 자동 재시작 정책(`restart: unless-stopped` + 백오프)
   - 로그/메트릭 라벨 분리로 원인 추적성 강화

3. **API 가용성 보강**
   - uvicorn worker 최소 2개 이상(또는 gunicorn+uvicorn workers)로 상향
   - readiness/liveness를 분리해 부분장애 시 트래픽 차단

4. **장애 시나리오 리허설**
   - API 강제 종료, scheduler 강제 종료, DB 일시 장애 3종 테스트
   - 각 경우 주문/리스크 가드/재시작 복구 시간을 기록
   - 결과를 `docs/runbooks/failure-isolation.md`에 반영

5. **확장 포인트 기준 문서화**
   - 비동기 작업이 증가하면 큐(Kafka/RabbitMQ/Redis Streams) 도입 기준을 문서화
   - "큐 도입 임계치"를 처리량/지연/SLA 기준으로 수치화

| 항목 | 값 |
|---|---|
| 상태 | ⏳ 대기 |
| 근거 절 | 3.2, 3.3 |
| 예상 산출물 | compose 서비스 분리, `docs/runbooks/failure-isolation.md`, 장애 시나리오 테스트 결과 |

---

#### 블록 B: TLS 종단 + 네트워크 접근통제 기본선

**근거**: §3.4 보안/인증/권한

backend가 plain HTTP(8000)로 동작하는 현재 구조를 기준으로 TLS와 접근제어를 단계 적용한다.

**실행 항목**:

1. **외부 TLS 종단**
   - Nginx 또는 LB에서 443 종단, 80→443 강제 리다이렉트
   - 인증서 자동 갱신(Managed cert 또는 ACME) 구성
   - HSTS, TLS 1.2+ only, 강한 cipher suite 적용

2. **백엔드 노출 최소화**
   - backend 컨테이너는 외부 노출 금지, 내부 네트워크 전용
   - 외부 진입점은 reverse proxy 하나로 통일
   - 방화벽에서 8000 직접 접근 차단

3. **서비스 간 접근통제**
   - Docker 네트워크 분리 (예: `edge_net`, `app_net`, `data_net`)
   - Postgres/Redis는 `data_net`에서 backend만 접근 가능하도록 제한
   - 불필요 포트 publish 제거

4. **전송구간 보강 (가능 범위)**
   - 내부 mTLS가 당장 어렵다면 최소한 DB 연결 TLS 옵션 활성화
   - 비밀정보 전송/로그 출력 마스킹 점검

5. **KIS ws:// 리스크 완화**
   - `ws://` 사용 구간을 명시적으로 분리하고, boot guard 정책 문서화 (✅ 2026-04-11 완료)
   - 비정상 프레임/재연결 폭주/도메인 불일치 탐지 룰을 모니터링에 추가
   - KIS `wss://` 지원 시 즉시 전환할 코드 토글 위치를 문서에 명시

| 항목 | 값 |
|---|---|
| 상태 | ⏳ 대기 |
| 근거 절 | 3.4 |
| 예상 산출물 | nginx TLS 설정, Docker 네트워크 분리, 방화벽 규칙, `docs/security/network-hardening.md` |

---

#### 블록 C: DR 수용 기준(Acceptance Criteria) 정의

**근거**: §3.3 신뢰성/가용성

현재 DR 필요성은 인식되어 있으나, 성공/실패 판정 기준(RTO/RPO, 데이터 정합성 체크리스트, 복구 후 트레이딩 재개 조건)이 명시되지 않으면 드릴이 반복돼도 개선이 어렵다.

**실행 항목**:

`docs/runbooks/` 또는 DR 관련 문서 위치에 "DR acceptance criteria" 섹션을 추가한다. 최소 포함 항목:

1. 저장소별 목표 RPO/RTO (TimescaleDB, MongoDB, Redis)
2. 복구 성공 판정 SQL/쿼리 목록 (주문 수, 체결 수, 잔고 스냅샷 일치 등)
3. 복구 후 트레이딩 재개 전 필수 점검 (스케줄러 상태, 시계열 수집 정상 여부, 주문 차단 해제 조건)
4. 분기 1회 DR drill 결과 템플릿 (실측 RTO/RPO, 실패 원인, 후속 액션)

| 항목 | 값 |
|---|---|
| 상태 | ⏳ 대기 |
| 근거 절 | 3.3 |
| 예상 산출물 | `docs/runbooks/dr-acceptance-criteria.md`, 판정 쿼리 목록, 드릴 결과 템플릿 |

---

#### 블록 D: DR 드릴 자동화 (복구 리허설 스크립트)

**근거**: §3.3 신뢰성/가용성

`backup_db.sh` 및 WAL 아카이빙 경로를 기준으로 "복구 자동 리허설"을 스크립트화한다.

**실행 항목**:

1. **복구 기준선 정의**
   - RPO/RTO 목표를 저장소별로 명시 (예: Postgres 5분, Mongo 15분, Redis 15분)
   - 복구 성공 판정 지표: 주문 건수, 체결 건수, 잔고 스냅샷, 최근 시세 인덱스 정합성

2. **드릴 환경 구성**
   - 프로덕션과 분리된 DR 검증용 compose profile (`docker compose --profile dr up`) 사용
   - 백업 파일/GCS 아카이브를 내려받아 임시 볼륨에 복원
   - 참고: 별도 `compose.dr.yml`은 운영 compose와의 drift 위험이 있으므로 profile 방식 권장

3. **복구 자동화 스크립트**
   - `scripts/dr/restore_timescaledb.sh` (base backup + WAL replay 시점 복원)
   - `scripts/dr/restore_mongo.sh` (mongodump/mongorestore 또는 oplog point-in-time)
   - `scripts/dr/restore_redis.sh` (RDB/AOF 복원)
   - 전체 오케스트레이션 `scripts/dr/run_drill.sh`

4. **정합성 검증**
   - `scripts/dr/verify_consistency.py`로 3개 저장소 교차 검증
   - 결과 리포트(JSON/Markdown): 실측 RTO/RPO, 실패 단계, 로그 링크

5. **주기 운영**
   - DEMO 단계: 분기 1회 수동 드릴 (월 1회는 현재 오버헤드 과다)
   - 실자금 투입 시: 월 1회 자동화 스케줄 전환
   - 결과를 `docs/dr/drill-history/`에 누적
   - 실패 시 48시간 내 후속 조치 티켓 생성 규칙 추가

| 항목 | 값 |
|---|---|
| 상태 | ⏳ 대기 |
| 근거 절 | 3.3 |
| 예상 산출물 | `scripts/dr/*`, `docs/dr/drill-history/`, compose DR profile |

---

#### 블록 E: 시크릿 수명주기 관리 정책

**근거**: §3.4 보안/인증/권한, §3.11 규제/감사/컴플라이언스

배포/운영 문서와 설정 로딩 코드 경로를 기준으로 시크릿 분류표를 만든다.

**실행 항목**:

1. API 키/DB 비밀번호/서명키를 유형별로 분류
2. 각 항목에 회전 주기 및 책임자 지정
3. 런타임에서 평문 노출 방지 (로그 마스킹, env dump 차단)
4. 회전 후 헬스체크 및 롤백 절차를 runbook에 추가

| 항목 | 값 |
|---|---|
| 상태 | ⏳ 대기 |
| 근거 절 | 3.4, 3.11 |
| 예상 산출물 | `docs/security/secret-lifecycle.md`, 시크릿 분류 매트릭스, 로그 마스킹 검증 테스트 |

---

#### 블록 F: Terraform 기반 단일 서버 재현 배포 (IaC v1)

**근거**: §3.10 배포/런타임 인프라

`infra/terraform/` 디렉터리를 만들고, 현재 GCP 단일 서버 구성을 코드로 선언한다.

**실행 항목**:

1. **상태/구조 초기화**
   - `infra/terraform/main.tf`, `variables.tf`, `outputs.tf`, `providers.tf` 생성
   - GCS backend(원격 state) 사용. 현재 단독 운영이므로 state lock은 GCS 버전관리+운영 규칙으로 보완 (팀 확장 시 lock 메커니즘 필수 — README에 명시)
   - 환경 분리: `envs/dev`, `envs/prod` tfvars 구성

2. **GCP 리소스 선언**
   - VPC, 서브넷, 방화벽 (22 제한, 80/443, 내부 포트 제한)
   - 단일 VM (현재 스펙 반영: 4 core, 8GB RAM, 50GB SSD), 고정 IP, 서비스 계정 최소권한
   - OS Login 또는 SSH 키 관리 방식을 코드화

3. **부트스트랩 자동화**
   - VM startup script 또는 cloud-init으로 Docker/Compose 설치
   - `docker-compose.yml` 배포 경로/권한/systemd 서비스 등록
   - 운영 계정/디렉터리(`/opt/aqts`) 표준화

4. **배포 파이프라인 연결**
   - CI에서 `terraform fmt/validate/plan` 실행
   - 승인 후 `terraform apply` (prod는 수동 승인 게이트)
   - apply 아티팩트/plan 파일 보관

5. **재현성 검증**
   - 기존 서버와 동일 구성으로 신규 VM 생성 후 서비스 기동
   - "서버 손실 시 복구" 리허설: 빈 프로젝트에서 IaC만으로 동일 환경 재구축
   - 검증 결과를 `docs/runbooks/rebuild-from-zero.md`에 기록

| 항목 | 값 |
|---|---|
| 상태 | ⏳ 대기 |
| 근거 절 | 3.10 |
| 예상 산출물 | `infra/terraform/`, CI terraform plan 워크플로, `docs/runbooks/rebuild-from-zero.md` |

---

#### 블록 G: Canary/Blue-Green 배포를 실제 Compose 경로에 연결

**근거**: §3.3 신뢰성/가용성, §3.10 배포/런타임 인프라

`nginx-canary.conf`가 존재하나 실사용 경로에 연결되지 않았다. `docker-compose.yml`과 Nginx 라우팅을 일관되게 묶는다.

**전제**: 블록 A(scheduler 분리)가 선행되어야 리소스 여유가 생김. 현재 단일 서버(8GB RAM, 11개 컨테이너)에서 blue/green 두 백엔드를 동시에 띄우면 메모리 부담이 크므로, v1에서는 "blue 중단 → green 기동 → health check → 실패 시 blue 복귀"의 rolling 방식이 현실적이다. canary 비율(5→25→100) 방식은 서버 확장 후로 미룬다.

**실행 항목**:

1. **서비스 이원화**
   - compose에 `backend_blue`, `backend_green` 서비스 정의 (이미지 태그만 다르게)
   - 공통 환경변수는 anchor/extends로 중복 제거

2. **트래픽 스위칭**
   - Nginx upstream을 blue/green 두 그룹으로 정의
   - v1: rolling 방식 (blue 중단 → green 기동 → health → rollback-if-fail)
   - v2(서버 확장 후): canary 비율 또는 헤더 기반 라우팅
   - `nginx -t` 검증 후 무중단 reload 스크립트 준비

3. **배포 절차 문서화**
   - 새 버전은 green 기동 → 헬스체크 통과 → 전환 → 실패 시 즉시 blue 롤백
   - 문서 위치: `docs/runbooks/deploy-blue-green.md`

4. **자동 검증**
   - 배포 파이프라인에 "두 백엔드 동시 헬스체크 + 라우팅 확인" 단계 추가
   - 실패 시 자동 중단 및 기존 슬롯 유지

| 항목 | 값 |
|---|---|
| 상태 | ⏳ 대기 |
| 근거 절 | 3.3, 3.10 |
| 선행 조건 | 블록 A (scheduler 분리), 블록 F (IaC) |
| 예상 산출물 | compose blue/green 서비스, nginx 라우팅, `docs/runbooks/deploy-blue-green.md` |

---

#### 블록 H: 장애 격리 토폴로지 문서화

**근거**: §3.2 아키텍처/확장성

현재 compose 기준으로 서비스 책임 경계를 문서화하고, 최소 변경으로 분리 가능한 배포안을 만든다.

**실행 항목**:

1. scheduler를 독립 컨테이너/프로세스로 분리하는 실제 실행 구성 (블록 A에서 구현, 여기서는 문서화)
2. backend worker 수/메모리 제한/재시작 정책 명시
3. 한 컴포넌트 장애 시 다른 컴포넌트 영향 범위 표 (장중 주문, 리스크 체크, 데이터 수집)
4. 단계적 전환 순서 (관측지표 추가 → shadow 분리 → 트래픽 전환)

| 항목 | 값 |
|---|---|
| 상태 | ⏳ 대기 |
| 근거 절 | 3.2 |
| 선행 조건 | 블록 A 완료 후 실측 데이터 기반으로 작성 |
| 예상 산출물 | `docs/architecture/service-topology.md`, 장애 영향 범위 매트릭스 |

---

#### 블록 I: 4-eyes 주문 승인 + 감사 불변성 강제

**근거**: §3.7 트레이딩 운영 통제, §3.11 규제/감사/컴플라이언스

`enterprise-gap-roadmap.md`의 기존 P2 #11을 구체화한다.

**전제**: 현재 단일 사용자(`admin`)로 운영 중이므로, RBAC wiring 완전 적용(P2 #9 잔여) → 멀티유저 운영 → 4-eyes 순서로 진행한다. 단독 운영 동안에는 "고위험 주문에 대한 지연 실행 + 수동 확인 알림(Telegram)"이 4-eyes의 경량 대안이다.

**실행 항목**:

1. **감사로그 불변 저장**
   - 기존 DB 감사로그를 1차 저장소로 유지하되, 변경불가 아카이브를 병행 저장
   - 오브젝트 스토리지 retention lock(WORM) 버킷에 주기적 append-only 적재
   - 각 배치에 해시체인(이전 해시 포함)으로 위변조 탐지 가능하게 설계

2. **이벤트 스키마 표준화**
   - `event_id`, `actor`, `action`, `resource`, `before/after`, `reason`, `trace_id`, `signature` 필드 고정
   - 서비스 전반에서 동일 스키마 사용 (주문/설정변경/권한변경/배포 이벤트 포함)

3. **4-eyes 정책 엔진**
   - "고위험 주문" 기준 (금액/변동성/레버리지/시장상태) 코드화
   - 조건 충족 시 상태를 `PENDING_APPROVAL`로 고정하고 단일 승인으로 실행 불가 처리
   - 승인자는 요청자와 다른 계정이어야 하며 역할검증(RBAC) 필수

4. **실행 차단 지점 구현**
   - 주문 실행 함수 직전에 승인 토큰/승인 이력 검증
   - 검증 실패 시 하드 차단(우회 불가) + 감사로그 기록

5. **감사/규제 리포트**
   - 월별 승인 이력/거절 사유/우회 시도 리포트 자동 생성
   - 감사 추적 조회 API는 읽기 전용, 삭제/수정 엔드포인트 금지
   - 관련 문서: `docs/compliance/4-eyes-and-audit.md`

| 항목 | 값 |
|---|---|
| 상태 | ⏳ 대기 |
| 근거 절 | 3.7, 3.11 |
| 선행 조건 | P2 #9 RBAC wiring 완전 적용, 멀티유저 운영 전환 |
| 예상 산출물 | `OrderApproval` 모델, WORM 어댑터, 이벤트 스키마, `docs/compliance/4-eyes-and-audit.md` |

---

### 후순위 (P3 이후 또는 별도 트랙)

- 데이터 품질 SLO 대시보드 (3.5)
- 모델 드리프트 자동 롤백/디그레이드 (3.6)
- 멀티 브로커 failover routing (3.7)
- SLI/SLO error budget 릴리즈 게이트 연동 (3.8)
- 성능 회귀 벤치마크 게이트 (3.9)
- 멀티테넌시/멀티유저 지원 (3.1)

---

## 5) 결론

AQTS는 기능 구현/테스트 폭 측면에서 개인·소규모 실운영 시스템으로 매우 우수하다. 기업단위 운영의 핵심은 "기능 존재"가 아니라 "통제/보안/가용성/거버넌스의 강제력"이며, 현재는 **기능 성숙도 高 / 엔터프라이즈 강제력 中 이하**.

P0~P1(1~7위)은 모두 완료되어 사고 예방·운영 안정화 1차 라운드가 끝났다. P2(8~10위) 역시 문서 SSOT 자동화, RBAC, SBOM/이미지 서명이 완료되었다.

다음 단계인 P3(블록 A~I)는 **DEMO에서 프로덕션으로의 전환 준비** 단계로, 권장 실행 순서는: Scheduler 분리(A) → TLS/네트워크(B) → DR 기준+드릴(C+D) → 시크릿 관리(E) → IaC(F) → 배포 전략+토폴로지(G+H) → 4-eyes/감사(I) 이다. 블록 A~D까지 완료하면 "서비스가 죽어도 복구할 수 있고, 외부 공격에 노출되지 않으며, 한 컴포넌트 장애가 전파되지 않는" 최소 운영 안전선이 확보된다.

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

## 부록 B. P3 블록 의존성 그래프

```
블록 A (Scheduler 분리)
  ├──→ 블록 B (TLS + 네트워크)
  │      └──→ 블록 E (시크릿 수명주기)
  │             └──→ 블록 F (Terraform IaC)
  │                    ├──→ 블록 G (Blue-Green 배포)
  │                    └──→ 블록 H (장애 격리 문서)
  └──→ 블록 C+D (DR 기준 + 드릴)
                                └──→ 블록 I (4-eyes + 감사 불변성)
                                       ↑ 선행: P2 #9 RBAC wiring 완전 적용
```

## 부록 C. P3 추가 배경 (2026-04-12)

2026-04-11~12 Phase 1 DEMO 검증 과정에서 수행한 보안 감사(6항목) 및 코드베이스 전반 갭 분석 결과, 5개 핵심 영역(인프라 자동화, DR 검증, 네트워크 보안, SPOF, 규제/감사)이 식별되었다. 전문가 리뷰를 거쳐 9개 실행 블록으로 구체화하였으며, 각 블록은 독립 머지 가능한 워크 패키지로 설계되었다. 실행 순서는 현재 상태(DEMO, 단일 GCP 서버, 단독 운영자)에서 리스크 대비 효과가 큰 순서로 배치하였다.
