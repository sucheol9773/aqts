# RBAC 권한 매트릭스 (Wiring 검증 기준)

본 문서는 AQTS API 의 모든 라우트에 대한 RBAC 가드 매트릭스다. `scripts/check_rbac_coverage.py` 정적 검사와 `tests/test_rbac_routes.py` 통합 테스트가 본 매트릭스의 정합성을 강제한다.

## 역할 위계

`viewer < operator < admin` — 상위 역할은 하위 역할의 모든 권한을 포함한다(`require_viewer` 가드는 operator/admin 도 통과).

## 가드 분류 원칙

- **read (GET)** → `require_viewer` 이상 명시
- **mutation (POST/PUT/PATCH/DELETE)** → `require_operator` 이상 명시. viewer-only 는 정적 검사에서 error.
- **admin-only** → 사용자 관리, 시스템 설정, 감사 로그 조회
- **자기 세션 관리** → `/api/auth/{login,refresh,logout,me,mfa/*}` 는 화이트리스트(가드 면제, 토큰 자체로만 검증)

## 권한 매트릭스

| 라우터 | 메서드 | 경로 | 가드 |
|---|---|---|---|
| alerts | GET | /api/alerts/ | require_viewer |
| alerts | GET | /api/alerts/stats | require_viewer |
| alerts | PUT | /api/alerts/{alert_id}/read | require_operator |
| alerts | PUT | /api/alerts/read-all | require_operator |
| audit | GET | /api/audit/decisions/ | require_viewer |
| audit | GET | /api/audit/decisions/{decision_id} | require_viewer |
| audit | POST | /api/audit/decisions/{decision_id} | require_operator |
| audit | POST | /api/audit/decisions/{decision_id}/steps/{step_name} | require_operator |
| auth | POST | /api/auth/login | (whitelist) |
| auth | POST | /api/auth/refresh | (whitelist) |
| auth | POST | /api/auth/logout | (whitelist, token only) |
| auth | GET | /api/auth/me | (whitelist, token only) |
| auth | POST | /api/auth/mfa/enroll | (whitelist, token only) |
| auth | POST | /api/auth/mfa/verify | (whitelist, token only) |
| auth | POST | /api/auth/mfa/disable | (whitelist, token only) |
| dry_run | POST | /api/dry-run/start | require_operator |
| dry_run | POST | /api/dry-run/stop | require_operator |
| dry_run | DELETE | /api/dry-run/sessions | require_operator |
| dry_run | GET | /api/dry-run/status | require_viewer |
| dry_run | GET | /api/dry-run/report | require_viewer |
| dry_run | GET | /api/dry-run/sessions/{session_id} | require_viewer |
| ensemble | GET | /api/ensemble/cached | require_viewer |
| ensemble | GET | /api/ensemble/cached/{ticker} | require_viewer |
| ensemble | POST | /api/ensemble/run | require_operator |
| ensemble | POST | /api/ensemble/batch | require_operator |
| market | GET | /api/market/exchange-rate | require_viewer |
| market | GET | /api/market/indices | require_viewer |
| market | GET | /api/market/economic-indicators | require_viewer |
| market | GET | /api/market/universe | require_viewer |
| oos | POST | /api/system/oos/run | require_operator |
| oos | GET | /api/system/oos/latest | require_viewer |
| oos | GET | /api/system/oos/gate-status | require_viewer |
| oos | GET | /api/system/oos/{run_id} | require_viewer |
| orders | GET | /api/orders/ | require_viewer |
| orders | GET | /api/orders/{order_id} | require_viewer |
| orders | POST | /api/orders/ | require_operator |
| orders | POST | /api/orders/batch | require_operator |
| orders | DELETE | /api/orders/{order_id} | require_operator |
| param_sensitivity | POST | /api/param-sensitivity/run | require_operator |
| param_sensitivity | GET | /api/param-sensitivity/latest | require_viewer |
| param_sensitivity | GET | /api/param-sensitivity/tornado | require_viewer |
| portfolio | GET | /api/portfolio/summary | require_viewer |
| portfolio | GET | /api/portfolio/positions | require_viewer |
| portfolio | GET | /api/portfolio/performance | require_viewer |
| portfolio | GET | /api/portfolio/value-history | require_viewer |
| profile | GET | /api/profile/ | require_viewer |
| profile | PUT | /api/profile/ | require_operator |
| realtime | GET | /api/realtime/quotes | require_viewer |
| realtime | GET | /api/realtime/quotes/{ticker} | require_viewer |
| realtime | GET | /api/realtime/status | require_viewer |
| system | GET | /api/system/settings | require_admin |
| system | GET | /api/system/audit-logs | require_admin |
| system | GET | /api/system/circuit-breakers | require_viewer |
| system | POST | /api/system/backtest | require_operator |
| system | POST | /api/system/rebalancing | require_operator |
| system | POST | /api/system/pipeline | require_operator |
| users | GET | /api/users | require_admin |
| users | GET | /api/users/{user_id} | require_admin |
| users | POST | /api/users | require_admin |
| users | PATCH | /api/users/{user_id} | require_admin |
| users | POST | /api/users/{user_id}/password-reset | require_admin |
| users | POST | /api/users/{user_id}/lock | require_admin |
| users | DELETE | /api/users/{user_id} | require_admin |

## 강제 검사

1. **정적 검사**: `python scripts/check_rbac_coverage.py` (Doc Sync 워크플로 등록)
2. **통합 테스트**: `pytest tests/test_rbac_routes.py` — viewer 토큰으로 전 mutation 라우트 호출 → 403 검증
3. **수동 검증**: 신규 라우트 PR 머지 전 viewer 토큰으로 직접 호출하여 403 확인

## 신규 라우트 추가 절차

1. 라우트 정의 시 `Depends(require_viewer|operator|admin)` 명시
2. 본 매트릭스에 행 추가
3. `python scripts/check_rbac_coverage.py` 실행 → PASS 확인
4. `pytest tests/test_rbac_routes.py` 실행 → 자동으로 신규 라우트가 viewer 403 검증에 포함되는지 확인
5. PR 설명에 매트릭스 변경 명시
