# AQTS API Contracts

> `backend/api/routes/*.py` 15개 라우터의 **HTTP 계약 인덱스**. 각 엔드포인트의 풀패스·메서드·파일:라인·RBAC 가드를 한 장에 모읍니다. 상세 요청/응답 스키마는 `backend/api/schemas/` 와 각 라우터 파일 본문을 단일 진실원천으로 삼습니다.
>
> RBAC 정책(authn ≠ authz 분리, Wiring Rule) 은 [development-policies.md §12](./development-policies.md) 에 정의되며, 권한 매트릭스의 심층 이력은 `docs/security/rbac-policy.md` / `rbac.md` 를 참조합니다.

---

## 1. 라우터 마운트 (`backend/main.py:578-598`)

| Prefix | 파일 | 태그 |
|---|---|---|
| `/api/auth` | `backend/api/routes/auth.py` | `Auth` |
| `/api` | `backend/api/routes/users.py` | `Users` |
| `/api/portfolio` | `backend/api/routes/portfolio.py` | `Portfolio` |
| `/api/orders` | `backend/api/routes/orders.py` | `Orders` |
| `/api/profile` | `backend/api/routes/profile.py` | `Profile` |
| `/api/market` | `backend/api/routes/market.py` | `Market` |
| `/api/alerts` | `backend/api/routes/alerts.py` | `Alerts` |
| `/api/system` | `backend/api/routes/system.py` | `System` |
| `/api/system/oos` | `backend/api/routes/oos.py` | `OOS Validation` |
| `/api/system/param-sensitivity` | `backend/api/routes/param_sensitivity.py` | `Parameter Sensitivity` |
| `/api/audit` | `backend/api/routes/audit.py` | `audit` (라우터 자체 prefix, `include_router` 에 추가 prefix 없음) |
| `/api/ensemble` | `backend/api/routes/ensemble.py` | `Ensemble` |
| `/api/realtime` | `backend/api/routes/realtime.py` | `Realtime` |
| `/api/system/dry-run` | `backend/api/routes/dry_run.py` | `Dry Run` |

루트 레벨 엔드포인트 (`/health`, `/api/info`, `/dashboard`, `/metrics`) 는 `backend/main.py` 에 직접 정의된다.

---

## 2. 엔드포인트 인벤토리

> 표의 "RBAC" 열은 라우트 시그니처의 `Depends(...)` 를 그대로 옮긴 값입니다. 모든 mutation 라우트에는 `require_operator` 또는 `require_admin` 이 명시되어야 하며 (development-policies.md §12), 신규 라우트 추가 시 `python scripts/check_rbac_coverage.py` 0 errors 를 커밋 전에 반드시 확인합니다.

### 2.1 Auth (`/api/auth`, `auth.py`)

| 메서드 | 경로 | 라인 | RBAC | 용도 |
|---|---|---|---|---|
| POST | `/login` | `auth.py:33` | (공개) | 로그인 — `TokenResponse` |
| POST | `/refresh` | `auth.py:79` | 토큰 기반 | 액세스 토큰 갱신 |
| POST | `/logout` | `auth.py:194` | 토큰 기반 | 로그아웃 |
| GET | `/me` | `auth.py:226` | `get_current_user` | 현재 사용자 정보 (자기 세션 관리 예외, development-policies.md §12) |
| POST | `/mfa/enroll` | `auth.py:242` | 토큰 기반 | TOTP 등록 |
| POST | `/mfa/verify` | `auth.py:272` | 토큰 기반 | TOTP 검증 |
| POST | `/mfa/disable` | `auth.py:311` | 토큰 기반 | TOTP 비활성화 |

### 2.2 Users (`/api/users`, `users.py`)

| 메서드 | 경로 | 라인 | RBAC | 용도 |
|---|---|---|---|---|
| GET | `/users` | `users.py:34` | `require_admin` | 사용자 목록 |
| GET | `/users/{user_id}` | `users.py:72` | `require_admin` | 단일 조회 |
| POST | `/users` | `users.py:109` | `require_admin` | 생성 |
| PATCH | `/users/{user_id}` | `users.py:173` | `require_admin` | 수정 |
| POST | `/users/{user_id}/password-reset` | `users.py:256` | `require_admin` | 패스워드 초기화 |
| POST | `/users/{user_id}/…` | `users.py:294` | `require_admin` | (신원 관리 추가 액션) |
| DELETE | `/users/{user_id}` | `users.py:340` | `require_admin` | 삭제 |

### 2.3 Profile (`/api/profile`, `profile.py`)

| 메서드 | 경로 | 라인 | RBAC |
|---|---|---|---|
| GET | `/` | `profile.py:34` | `require_viewer` |
| PUT | `/` | `profile.py:68` | `require_operator` |

### 2.4 Portfolio (`/api/portfolio`, `portfolio.py`)

| 메서드 | 경로 | 라인 | RBAC |
|---|---|---|---|
| GET | `/summary` | `portfolio.py:33` | `require_viewer` |
| GET | `/positions` | `portfolio.py:117` | `require_viewer` |
| GET | `/performance` | `portfolio.py:175` | `require_viewer` |
| GET | `/value-history` | `portfolio.py:242` | `require_viewer` |
| POST | `/construct` | `portfolio.py:311` | `require_operator` |

### 2.5 Orders (`/api/orders`, `orders.py`)

| 메서드 | 경로 | 라인 | RBAC |
|---|---|---|---|
| POST | `/` | `orders.py:138` | `require_operator` |
| POST | `/batch` | `orders.py:283` | `require_operator` |
| GET | `/` | `orders.py:438` | `require_viewer` |
| GET | `/{order_id}` | `orders.py:504` | `require_viewer` |
| DELETE | `/{order_id}` | `orders.py:553` | `require_operator` |

주문 접수 경로는 TradingGuard pre-order 체크와 연동된다 (`backend/core/trading_guard.py:335` `check_pre_order`).

### 2.6 Market (`/api/market`, `market.py`)

| 메서드 | 경로 | 라인 | RBAC |
|---|---|---|---|
| GET | `/exchange-rate` | `market.py:24` | `require_viewer` |
| GET | `/indices` | `market.py:49` | `require_viewer` |
| GET | `/economic-indicators` | `market.py:123` | `require_viewer` |
| GET | `/universe` | `market.py:176` | `require_viewer` |

### 2.7 Alerts (`/api/alerts`, `alerts.py`)

| 메서드 | 경로 | 라인 | RBAC |
|---|---|---|---|
| GET | `/` | `alerts.py:29` | `require_viewer` |
| GET | `/stats` | `alerts.py:65` | `require_viewer` |
| PUT | `/{alert_id}/read` | `alerts.py:84` | `require_operator` |
| PUT | `/read-all` | `alerts.py:103` | `require_operator` |

알림 상태 머신 및 `NotificationRouter wired` 로그는 [architecture.md §7](./architecture.md) 및 development-policies.md §14 참조.

### 2.8 System (`/api/system`, `system.py`)

| 메서드 | 경로 | 라인 | RBAC |
|---|---|---|---|
| GET | `/settings` | `system.py:54` | `require_admin` |
| POST | `/backtest` | `system.py:90` | `require_operator` |
| POST | `/rebalancing` | `system.py:293` | `require_operator` |
| GET | `/rebalancing/status/{task_id}` | `system.py:505` | `require_viewer` |
| POST | `/pipeline` | `system.py:537` | `require_operator` |
| GET | `/audit-logs` | `system.py:625` | `require_admin` |
| GET | `/circuit-breakers` | `system.py:684` | `require_viewer` |
| GET | `/kill-switch/status` | `system.py:758` | `require_viewer` |
| POST | `/kill-switch/deactivate` | `system.py:784` | `require_admin` |

kill switch 해제는 `require_admin` 로 엄격하게 제한된다 (`system.py:784-790`). 이는 TradingGuard state 의 `kill_switch_on` 플래그를 되돌리는 경로다.

### 2.9 OOS Validation (`/api/system/oos`, `oos.py`)

| 메서드 | 경로 | 라인 | RBAC |
|---|---|---|---|
| POST | `/run` | `oos.py:67` | `require_operator` |
| GET | `/latest` | `oos.py:141` | `require_viewer` |
| GET | `/gate-status` | `oos.py:165` | `require_viewer` |
| GET | `/{run_id}` | `oos.py:181` | `require_viewer` |

### 2.10 Parameter Sensitivity (`/api/system/param-sensitivity`, `param_sensitivity.py`)

| 메서드 | 경로 | 라인 | RBAC |
|---|---|---|---|
| POST | `/run` | `param_sensitivity.py:32` | `require_operator` |
| GET | `/latest` | `param_sensitivity.py:70` | `require_viewer` |
| GET | `/tornado` | `param_sensitivity.py:86` | `require_viewer` |

### 2.11 Audit (`/api/audit`, `audit.py`)

`router = APIRouter(prefix="/api/audit", tags=["audit"])` 로 파일 자체 prefix 를 사용한다 (`audit.py:15`). `backend/main.py:592` 에서는 `include_router(audit.router)` 만 호출한다.

| 메서드 | 경로 | 라인 | RBAC |
|---|---|---|---|
| POST | `/decisions/{decision_id}` | `audit.py:20` | `require_operator` |
| GET | `/decisions/{decision_id}` | `audit.py:43` | `require_viewer` |
| GET | `/decisions/` | `audit.py:70` | `require_viewer` |
| POST | `/decisions/{decision_id}/steps/{step_name}` | `audit.py:114` | `require_operator` |

### 2.12 Ensemble (`/api/ensemble`, `ensemble.py`)

| 메서드 | 경로 | 라인 | RBAC |
|---|---|---|---|
| GET | `/cached` | `ensemble.py:40` | `require_viewer` |
| GET | `/cached/{ticker}` | `ensemble.py:79` | `require_viewer` |
| POST | `/run` | `ensemble.py:137` | `require_operator` |
| POST | `/batch` | `ensemble.py:184` | `require_operator` |

### 2.13 Realtime (`/api/realtime`, `realtime.py`)

| 메서드 | 경로 | 라인 | RBAC |
|---|---|---|---|
| GET | `/quotes` | `realtime.py:21` | `require_viewer` |
| GET | `/quotes/{ticker}` | `realtime.py:49` | `require_viewer` |
| GET | `/status` | `realtime.py:78` | `require_viewer` |

### 2.14 Dry Run (`/api/system/dry-run`, `dry_run.py`)

| 메서드 | 경로 | 라인 | RBAC |
|---|---|---|---|
| POST | `/start` | `dry_run.py:27` | `require_operator` |
| POST | `/stop` | `dry_run.py:60` | `require_operator` |
| GET | `/status` | `dry_run.py:86` | `require_viewer` |
| GET | `/report` | `dry_run.py:124` | `require_viewer` |
| GET | `/sessions/{session_id}` | `dry_run.py:140` | `require_viewer` |
| DELETE | `/sessions` | `dry_run.py:168` | `require_operator` |

---

## 3. 루트 레벨 엔드포인트 (`backend/main.py`)

| 메서드 | 경로 | 라인 | 용도 |
|---|---|---|---|
| GET | `/health` | `backend/main.py:428` | 헬스체크 |
| GET | `/api/info` | `backend/main.py:552` | 메타 정보 |
| GET | `/dashboard` | `backend/main.py:563` | 정적 대시보드 (HTML) |
| GET | `/metrics` | FastAPI + prometheus_client | `aqts_alert_dispatch_*` 포함 (development-policies.md §14.2) |

---

## 4. 신규 라우트 추가 시 워크플로

1. `backend/api/routes/<file>.py` 에 라우트 정의. mutation 은 `require_operator`/`require_admin`, read 는 `require_viewer` 의존성을 명시한다.
2. 해당 파일을 `backend/main.py` 의 `include_router` 블록에 등록한다 (이미 포함된 파일이면 이 단계 skip).
3. `docs/security/rbac-policy.md` 의 권한 매트릭스에 새 경로를 추가한다.
4. `backend/tests/test_rbac_routes.py` 에 viewer=403 / admin=200 통합 테스트를 추가하거나 매트릭스 자동 검증이 신규 경로를 커버하는지 확인한다.
5. `python scripts/check_rbac_coverage.py` 실행 — **0 errors** 여야 한다.
6. 커밋 전 검증 (development-policies.md §3): ruff / black / pytest.
7. 본 문서(해당 라우터 섹션) 에 새 경로를 추가하고, 필요 시 [architecture.md](./architecture.md) 에도 반영한다.

---

## 5. 공통 주의사항

- 응답 공통 포맷은 `api.schemas.common.APIResponse[T]` 입니다. 신규 라우트는 특별한 이유가 없는 한 동일 래퍼를 사용합니다.
- 에러 처리는 `backend/main.py:383` `_standard_http_exception_handler` 가 공용 경로를 담당합니다. 라우트 단에서 `HTTPException` 을 던지면 표준 JSON 으로 직렬화됩니다.
- `Depends(get_current_user)` 직접 사용은 `auth.py` 의 자기 세션 관리 엔드포인트에 한정합니다 (development-policies.md §12). 다른 파일에 나타나면 RBAC Wiring Rule 위반이며, 정적 검사(`check_rbac_coverage.py`) 에서 걸립니다.

---

## 문서 소유권

- 라우트 수·경로가 변경되면 본 문서와 `docs/security/rbac-policy.md` 두 곳을 동시에 갱신합니다.
- 현재 총 라우트 수 (Auth 7 + Users 7 + Profile 2 + Portfolio 5 + Orders 5 + Market 4 + Alerts 4 + System 9 + OOS 4 + Param Sensitivity 3 + Audit 4 + Ensemble 4 + Realtime 3 + Dry Run 6 = **67개**) 가 변하면 FEATURE_STATUS.md 의 대응 카운트도 갱신해야 합니다.
