# 릴리스 승인 게이트 (Release Approval Gates)

**문서 번호**: OPS-004
**버전**: 1.11
**최종 수정**: 2026-04-05

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
| 단위 테스트 전체 통과 | pytest 0 failures | PASS (2,477건 통과) |
| 코드 커버리지 | >= 80% | PASS (82%) |
| 린트/포맷 검사 | ruff/black 위반 0건 | PASS (ruff 0.15.9 + black 26.3.1, 위반 0건) |
| 의존성 취약점 | pip-audit critical 0건 | PASS (starlette CVE 해소, torch CPU 인덱스 설치로 2.6.0+ 적용 — Dockerfile 반영 완료) |
| API 계약 테스트 | Pydantic 스키마 검증 | PASS (9개 계약) |
| 통합 테스트 | 주요 플로우 E2E | PASS (30건 + OOS 55건 + 민감도 40건 + 인프라 70건) |
| 문서 동기화 | FEATURE_STATUS 최신화 | PASS |

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
Gate A: PASS (torch 2.6.0+ CPU 인덱스 Dockerfile 반영, 전 항목 통과)
Gate B: PASS (torch CVE 해소, 보안 전 항목 통과)
Gate C: PASS (알림 채널 검증 + 백업 알림 구현 완료)
Gate D: PASS (감사/보존/PII/리포트/비밀키 전 항목 통과, 97 tests)
Gate E: PASS (ASC 운영책임자 서명 완료, 2026-04-05)
```

**결론: Gate A~E 전 게이트 PASS. 배포 승인 완료.**

### 변경 이력
- v1.11 (2026-04-05): Gate A~E 전 게이트 PASS — torch CPU Dockerfile 반영으로 Gate A/B 해소, 운영책임자(ASC) Gate E 서명 완료, 배포 스크립트(deploy.sh, verify_deployment.sh) 추가
- v1.10 (2026-04-05): 인프라 계층 mock 테스트 추가 (database/settings/constants/logging/audit_log, 70 tests), Implemented 6→1, 테스트 2,477건
- v1.9 (2026-04-05): FastAPI 0.135.3 + starlette 1.0.0 CVE 해소, audit_visualization 구현 (31 tests), Not Started 0건, 테스트 2,407건
- v1.8 (2026-04-05): Gate E 고객 공지 PASS + 롤백 계획 PASS + 모니터링 대시보드 PASS (53 tests), 테스트 2,376건, Gate E → CONDITIONAL
- v1.7 (2026-04-05): Gate D 규제 리포트 PASS + 비밀키 관리 PASS (40 tests), 테스트 2,323건, Gate D → PASS
- v1.6 (2026-04-05): Gate D 감사 로그 무결성 PASS + 거래 기록 보존 PASS + PII 마스킹 PASS (57 tests), 테스트 2,283건, Gate D → CONDITIONAL
- v1.5 (2026-04-05): Gate C 알림 채널 검증 PASS + 백업 알림 구현 (NotificationRouter: Telegram→File→Console 폴백, ChannelHealth 추적, 46 tests), 테스트 2,226건, Gate C → PASS
- v1.4 (2026-04-05): 파라미터 민감도 분석 모듈 PASS (OAT/Grid 스윕, 탄성치, 토네이도 차트, 40 tests), 테스트 2,180건
- v1.3 (2026-04-05): Gate B 시크릿 스캔 PASS, API 키 갱신 테스트 PASS, Gate C 손실 시뮬레이션 PASS, 매매 중단/재개 PASS, 테스트 2,140건
- v1.2 (2026-04-05): ruff/black 린트 PASS, pip-audit 실행 (aiohttp/jose/multipart CVE 해소, starlette/torch 잔여)
- v1.1 (2026-04-05): Rate Limiting PASS, Circuit Breaker PASS, OOS 파이프라인 PASS, 런북 완비, 테스트 2,088건 반영
- v1.0 (2026-04-04): 초판 작성
