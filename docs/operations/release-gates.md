# 릴리스 승인 게이트 (Release Approval Gates)

**문서 번호**: OPS-004
**버전**: 1.19
**최종 수정**: 2026-04-06

## 1. 목적

프로덕션 배포 전 5단계 승인 게이트를 통과해야 하며, 어느 하나라도 미통과 시 배포를 차단합니다.

## 2. 게이트 개요

```
Gate A (개발/QA) → Gate B (보안) → Gate C (리스크/운영) → Gate D (컴플라이언스) → Gate E (비즈니스)
     ↓                ↓                ↓                    ↓                    ↓
  코드 품질         보안 검증         운영 준비              규제 준수           사업 승인
```

**모든 게이트는 PASS/BLOCK/CONDITIONAL 중 하나를 반환합니다.**
- PASS: 다음 게이트로 진행
- BLOCK: 차단 사유 해소 후 재평가
- CONDITIONAL: 조건부 통과 (지정 기한 내 해소 필수)

## 3. Gate A — 개발/QA

| 항목 | 기준 | 현재 상태 |
|------|------|----------|
| 단위 테스트 전체 통과 | pytest 0 failures | PASS (3,847건 통과) |
| 코드 커버리지 | >= 80% | PASS (90%) |
| 린트/포맷 검사 | ruff/black 위반 0건 | PASS (ruff 0.15.9 + black 26.3.1, 위반 0건) |
| 의존성 취약점 | pip-audit critical 0건 | PASS (starlette CVE 해소, torch CPU 인덱스 설치로 2.6.0+ 적용 — Dockerfile 반영 완료) |
| API 계약 테스트 | Pydantic 스키마 검증 | PASS (9개 계약) |
| 통합 테스트 | 주요 플로우 E2E | PASS (30건 + OOS 55건 + 민감도 40건 + 인프라 70건 + 실시간 파이프라인 25건 + 드라이런 46건 + 백테스트개선 22건) |
| 문서 동기화 | FEATURE_STATUS 최신화 | PASS |
| CI/CD 파이프라인 | GitHub Actions 자동화 | PASS (Lint→Smoke→Test→Build→Deploy, 3개 워크플로우, 수동 승인 게이트 + 자동 롤백) |

**승인자**: 개발 리드

## 4. Gate B — 보안

| 항목 | 기준 | 현재 상태 |
|------|------|----------|
| .env 시크릿 미노출 | git log에 시크릿 없음 | PASS (git history 스캔 완료, 모든 시크릿은 mock 값) |
| CORS 설정 | 와일드카드(*) 미사용 | PASS |
| 인증/인가 | JWT 토큰 검증 | PASS (구현+테스트) |
| Rate Limiting | 로그인/API 제한 | PASS (slowapi, 4개 엔드포인트, 7 tests) |
| 컨테이너 보안 | non-root 실행 | PASS |
| 의존성 스캔 | 알려진 CVE 없음 | PASS (starlette 해소, torch CPU 인덱스 2.6.0+ Dockerfile 반영 완료) |
| API 키 만료/재발급 시나리오 | 정상 갱신 확인 | PASS (만료/갱신/경계값 10 tests) |

**승인자**: 보안 담당

## 5. Gate C — 리스크/운영

| 항목 | 기준 | 현재 상태 |
|------|------|----------|
| 손실 한도 시뮬레이션 | 일일 -3%, 주간 -5% 중단 동작 확인 | PASS (일일/MDD/연속/복합 22 tests) |
| 매매 중단/재개 테스트 | HALTED 전이 + 미체결 차단 확인 | PASS (전이/킬스위치 연동/복구 20 tests) |
| 알림 채널 검증 | Telegram 발송 성공 | PASS (발송 성공/실패/재시도/레벨필터 검증, 46 tests) |
| 백업 알림 | 1차 채널 장애 시 대체 동작 | PASS (NotificationRouter: Telegram→File→Console 폴백, ChannelHealth 추적) |
| Circuit Breaker | 외부 API 장애 시 자동 차단 | PASS (4개 서비스, 17 tests) |
| OOS 검증 파이프라인 | walk-forward OOS + Gate 판정 | PASS (55 tests) |
| 파라미터 민감도 분석 | OAT/Grid 스윕 + 탄성치 + 토네이도 차트 | PASS (40 tests, 6 모듈) |
| 온콜/인수인계 | 운영 매뉴얼 + 런북 완비 | PASS (5종 문서 완비) |

**승인자**: 운영책임자

## 6. Gate D — 컴플라이언스

| 항목 | 기준 | 현재 상태 |
|------|------|----------|
| 감사 로그 무결성 | 모든 주문/변경 기록 확인 | PASS (SHA-256 해시 체인, 변조 탐지, 57 tests) |
| 거래 기록 보존 | 5년 보존 설정 확인 | PASS (8개 카테고리, 5년/10년 보존, 조기 삭제 방지) |
| 리포트 템플릿 검증 | 규제 리포트 자동 생성 | PASS (4개 섹션 생성기, 종합 등급 산출, 40 tests) |
| 개인정보 점검 | 민감 데이터 암호화/마스킹 | PASS (7종 PII 탐지+마스킹, Settings 민감 필드 검증) |
| 비밀키 관리 | 키 로테이션/볼트 사용 | PASS (등록/로테이션/폐기/건강검사, 6종 시크릿 타입, 40 tests) |

**승인자**: 컴플라이언스 담당

## 7. Gate E — 비즈니스 승인

| 항목 | 기준 | 현재 상태 |
|------|------|----------|
| 고객 공지 문안 | 서비스 약관/면책 고지 준비 | PASS (투자 위험 고지, 데이터 처리 안내, SLA 정의) |
| 롤백 계획 | 배포 실패 시 복구 절차 문서화 | PASS (6단계 트리거, 앱/DB/설정 롤백, 검증 체크리스트) |
| 모니터링 대시보드 | 핵심 지표 실시간 확인 가능 | PASS (서비스 상태/메트릭/알림 통합, 53 tests) |
| 운영책임자 최종 승인 | 서명/승인 기록 | PASS (ASC 서명 완료, 2026-04-05) |

**승인자**: 경영진

### Gate E 최종 승인 서명란

| 항목 | 내용 |
|------|------|
| 승인자 성명 | ASC |
| 승인 일자 | 2026년 4월 5일 |
| Gate A~D 검토 확인 | [✓] 전 게이트 PASS 확인 |
| OPS-005 롤백 계획 검토 | [✓] 검토 완료 |
| OPS-006 고객 공지 검토 | [✓] 검토 완료 |
| 비상 연락망 확인 | [✓] 확인 완료 |
| 서명 | ASC |

> Gate E 서명 완료 후 Phase 0-4 최초 배포를 진행합니다.

## 8. 현재 게이트 통과 현황

```
Gate A: PASS (스트레스 테스트 28건 추가, 3,847건 통과, 90% 커버리지)
Gate B: PASS (torch CVE 해소, 보안 전 항목 통과)
Gate C: PASS (알림 채널 검증 + 백업 알림 구현 완료)
Gate D: PASS (감사/보존/PII/리포트/비밀키 전 항목 통과, 97 tests)
Gate E: PASS (ASC 운영책임자 서명 완료, 2026-04-05)
```

**결론: Gate A~E 전 게이트 PASS. 배포 승인 완료.**

### 변경 이력
- v1.28 (2026-04-07): 문서 SSOT 자동화 — `scripts/gen_status.py` 신설 (FEATURE_STATUS/README/release-gates의 테스트 수치를 backend/tests AST 카운트로 자동 갱신), `--check`/`--update`/`--print` 모드, changelog 라인 보존 정책, Doc Sync 워크플로 통합, enterprise-gap-roadmap 8위 완료, 14 tests 추가, 테스트 3,201건
- v1.27 (2026-04-07): 환경변수 bool 표기 표준화 — `core.utils.env.env_bool()` 단일 진입점, 표준 'true'/'false' 강제, 하위호환(1/0/yes/no/on/off) 경고 1회 + Prometheus counter `aqts_env_bool_nonstandard_total`, `AQTS_STRICT_BOOL` Phase 2 승격 스위치, 정적 검사 `scripts/check_bool_literals.py` (Doc Sync 워크플로 통합), `tracing.py`/`rate_limiter.py`/`main.py` ad-hoc 파싱 제거, conftest TESTING='true' 통일, 34 tests 추가, 테스트 3,200건
- v1.26 (2026-04-07): 관측성 고도화 — OpenTelemetry 분산 추적 (FastAPI/SQLAlchemy/httpx/Redis 자동 계측), OTel Collector + Jaeger docker-compose 서비스, trace_id 로그/응답 헤더 전파, NoOp fallback (graceful degradation), 28 tests 추가, 테스트 3,166건
- v1.25 (2026-04-07): 신뢰성/가용성 보강 + 아키텍처 분리 — DB 백업 자동화 (pg_dump/mongodump cron 컨테이너 + GCS 업로드 + 복원 스크립트), PostgreSQL PITR (WAL 아카이빙, wal_level=replica), 스케줄러 컨테이너 분리 (장애 격리, SCHEDULER_ENABLED 환경변수), 33 tests 추가, 테스트 3,138건
- v1.24 (2026-04-07): 보안 강화 — JWT key rotation (kid 헤더 + previous_secret_key), token revocation (jti + 인메모리 블랙리스트), 로그아웃 엔드포인트, bcrypt 전용 인증 (평문 fallback 제거), CD SSH 하드닝 (known_hosts 검증), 17 tests 추가, 테스트 3,105건
- v1.23 (2026-04-07): 엔터프라이즈 갭 대응 — Prometheus Alerting 체계 (5그룹 15규칙 + Alertmanager 텔레그램), Alembic DB 마이그레이션 초기 설정 (init_db.sql 베이스라인), docker-compose에 Alertmanager 서비스 추가, deployment-roadmap v1.3
- v1.22 (2026-04-07): Phase 0 카나리 배포 인프라 추가 — nginx split_clients 트래픽 분할 (nginx-canary.conf), docker-compose.canary.yml (stable/canary 듀얼 백엔드), canary_deploy.sh (start/promote/rollback/status/finish), pre_deploy_check.sh (7단계 자동 검증), deployment-roadmap.md v1.2 업데이트
- v1.21 (2026-04-07): 부하/스트레스 테스트 28건 추가 — 백테스트 스케일링 (1000일×50종목), 동시 백테스트 (ThreadPool 4건), 상태 머신 동시 전이, API 동시 요청 (20~50건), 파이프라인 동시 실행, 메모리 누수 검증, 서킷 브레이커 급속 트리거, 레짐 탐지 대량 데이터, 테스트 3,060→3,088건
- v1.20 (2026-04-07): 커버리지 부스트 90% 달성 — API 라우트 테스트 76건 (9개 모듈), 데이터 수집기 테스트 81건 (4개 모듈), 테스트 2,903→3,060건, 커버리지 85→90%
- v1.19 (2026-04-06): 백테스트 성능 종합 개선 — CRISIS 레짐 (5번째, 2/3 시그널), 변동성 스케일링 (vol_target), 점진적 재진입 (gradual_reentry_days), 동적 임계값 (레짐 기반), 22 tests 추가, 테스트 2,903건
- v1.18 (2026-04-06): 드라이런 엔진 추가 (DryRunEngine/Session/Order/Report, OrderExecutor dry_run 모드, 6개 API 엔드포인트), 46 tests 추가, 테스트 2,881건
- v1.16 (2026-04-06): CD 파이프라인 실전 전환 — 수동 승인 게이트, 자동 롤백, 배포 전 스냅샷, CI coverage threshold 60→80%
- v1.15 (2026-04-06): 커버리지 부스트 테스트 49건 추가 (engine 67→100%, pipeline 65→98%, data_loader 68→93%), 전체 커버리지 84→85%, 테스트 수 2,760→2,809
- v1.14 (2026-04-06): 실시간 파이프라인 E2E 통합 테스트 25건 추가 (마켓사이클/장애복원/RL블렌딩/레지스트리연동/Redis캐시/스케줄러상태/IntradayBar), 테스트 수 2,735→2,760
- v1.13 (2026-04-06): 테스트 수 2,477→2,735 반영 (RL v2 28 + RL production 20 + Realtime 20 = 68건 추가), 문서 정합성 일괄 수정
- v1.12 (2026-04-05): CI/CD GitHub Actions 파이프라인 추가 (ci.yml: Lint→Smoke→Test→Docker Build, cd.yml: GCP 자동 배포 + Telegram 알림), doc-sync-check.yml 정리
- v1.11 (2026-04-05): Gate A~E 전 게이트 PASS — torch CPU Dockerfile 반영으로 Gate A/B 해소, 운영책임자(ASC) Gate E 서명 완료, 배포 스크립트(deploy.sh, verify_deployment.sh) 추가
- v1.10 (2026-04-05): 인프라 계층 mock 테스트 추가 (database/settings/constants/logging/audit_log, 70 tests), Implemented 6→1, 테스트 2,477건
- v1.9 (2026-04-05): FastAPI 0.135.3 + starlette 1.0.0 CVE 해소, audit_visualization 구현 (31 tests), Not Started 0건, 테스트 2,407건
- v1.8 (2026-04-05): Gate E 고객 공지 PASS + 롤백 계획 PASS + 모니터링 대시보드 PASS (53 tests), 테스트 2,376건, Gate E → CONDITIONAL
- v1.7 (2026-04-05): Gate D 규제 리포트 PASS + 비밀키 관리 PASS (40 tests), 테스트 2,323건, Gate D → PASS
- v1.17 (2026-04-06): Prometheus + Grafana 모니터링 스택 추가 (메트릭 수집/시각화), JSON 구조화 로그 전환, 26 tests 추가, 테스트 2,835건
- v1.16 (2026-04-06): CD 파이프라인 실전 전환 — SSH bash -s 로그인 스크립트 우회, 자동 롤백, 스냅샷
- v1.6 (2026-04-05): Gate D 감사 로그 무결성 PASS + 거래 기록 보존 PASS + PII 마스킹 PASS (57 tests), 테스트 2,283건, Gate D → CONDITIONAL
- v1.5 (2026-04-05): Gate C 알림 채널 검증 PASS + 백업 알림 구현 (NotificationRouter: Telegram→File→Console 폴백, ChannelHealth 추적, 46 tests), 테스트 2,226건, Gate C → PASS
- v1.4 (2026-04-05): 파라미터 민감도 분석 모듈 PASS (OAT/Grid 스윕, 탄성치, 토네이도 차트, 40 tests), 테스트 2,180건
- v1.3 (2026-04-05): Gate B 시크릿 스캔 PASS, API 키 갱신 테스트 PASS, Gate C 손실 시뮬레이션 PASS, 매매 중단/재개 PASS, 테스트 2,140건
- v1.2 (2026-04-05): ruff/black 린트 PASS, pip-audit 실행 (aiohttp/jose/multipart CVE 해소, starlette/torch 잔여)
- v1.1 (2026-04-05): Rate Limiting PASS, Circuit Breaker PASS, OOS 파이프라인 PASS, 런북 완비, 테스트 2,088건 반영
- v1.0 (2026-04-04): 초판 작성
