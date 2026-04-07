# RBAC + MFA 정책 (v1.29+)

## 개요

AQTS 대시보드는 역할 기반 접근 제어(RBAC) + TOTP 다중 인증(MFA)을 지원합니다.

- **기본 역할**: viewer / operator / admin (3종)
- **인증**: username/password + 선택적 TOTP
- **계정 보호**: 5회 실패 시 자동 잠금
- **감시**: 모든 로그인/권한 변경 감사 로그 기록

---

## 역할 정의

### Viewer (조회 전용)

- **권한**: 모든 GET 엔드포인트 조회
- **금지**: 주문, 설정 변경, 사용자 관리

| 엔드포인트 | 메서드 | 접근 |
|-----------|--------|------|
| /api/portfolio/* | GET | ✓ |
| /api/market/* | GET | ✓ |
| /api/auth/me | GET | ✓ |
| /api/orders | POST | ✗ |
| /api/profile | PATCH | ✗ |

### Operator (실행 권한)

- **권한**: Viewer + 주문/리밸런싱 실행
- **기능**:
  - 주문 생성/취소
  - 리밸런싱 트리거
  - 백테스트 실행
  - OOS 검증

| 엔드포인트 | 메서드 | 접근 |
|-----------|--------|------|
| /api/portfolio/* | GET | ✓ |
| /api/orders | POST | ✓ |
| /api/orders/{id}/cancel | POST | ✓ |
| /api/system/rebalance | POST | ✓ |
| /api/system/backtest | POST | ✓ |
| /api/users | GET | ✗ |

### Admin (관리자)

- **권한**: Operator + 사용자/시스템 관리
- **기능**:
  - 모든 Operator 권한
  - 사용자 생성/삭제/역할 변경
  - 비밀번호 리셋
  - 계정 잠금/해제
  - 시스템 설정 변경
  - 감사 로그 조회

| 엔드포인트 | 메서드 | 접근 |
|-----------|--------|------|
| /api/users | GET | ✓ |
| /api/users | POST | ✓ |
| /api/users/{id} | PATCH | ✓ |
| /api/users/{id}/password-reset | POST | ✓ |
| /api/users/{id}/lock | POST | ✓ |
| /api/audit | GET | ✓ |
| /api/system/settings | PATCH | ✓ |

---

## 인증 흐름

### 1. 로그인 (username + password)

```bash
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "username": "john",
    "password": "secure-password",
    "totp_code": "123456"  # TOTP 활성화 시에만 필수
  }'
```

**응답** (성공):
```json
{
  "success": true,
  "data": {
    "access_token": "eyJ0eXA...",
    "refresh_token": "eyJ0eXA...",
    "expires_in": 86400
  },
  "message": "로그인 성공"
}
```

**응답** (실패):
```json
{
  "success": false,
  "detail": "Invalid username or password"  // 401
}
```

### 2. 토큰 갱신

Access Token 만료 시 Refresh Token으로 새 토큰 발급:

```bash
curl -X POST http://localhost:8000/api/auth/refresh \
  -H "Content-Type: application/json" \
  -d '{"refresh_token": "eyJ0eXA..."}'
```

### 3. 로그아웃

현재 토큰 무효화 (revocation):

```bash
curl -X POST http://localhost:8000/api/auth/logout \
  -H "Authorization: Bearer eyJ0eXA..."
```

---

## TOTP MFA (2FA)

### 활성화 절차

#### Step 1: MFA 등록 요청

```bash
curl -X POST http://localhost:8000/api/auth/mfa/enroll \
  -H "Authorization: Bearer <access_token>"
```

**응답**:
```json
{
  "success": true,
  "data": {
    "secret": "JBSWY3DP...",
    "provisioning_uri": "otpauth://totp/john@AQTS?secret=JBSWY3DP..."
  },
  "message": "TOTP 시크릿 생성됨. 인증기에 등록하세요."
}
```

#### Step 2: 인증기 앱에 등록

1. Google Authenticator, Authy, Microsoft Authenticator 등 앱 설치
2. `provisioning_uri`로 QR 코드 생성 또는 `secret` 수동 입력
3. 생성된 6자리 코드 확인

#### Step 3: MFA 검증

```bash
curl -X POST http://localhost:8000/api/auth/mfa/verify \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{"totp_code": "123456"}'
```

**응답**:
```json
{
  "success": true,
  "data": {"enabled": true},
  "message": "MFA가 활성화되었습니다."
}
```

### MFA 비활성화

```bash
curl -X POST http://localhost:8000/api/auth/mfa/disable \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{"password": "current-password"}'
```

---

## 사용자 관리 (Admin only)

### 사용자 목록 조회

```bash
curl -X GET http://localhost:8000/api/users \
  -H "Authorization: Bearer <admin_token>"
```

### 새 사용자 생성

```bash
curl -X POST http://localhost:8000/api/users \
  -H "Authorization: Bearer <admin_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "newuser",
    "password": "initial-password",
    "email": "user@example.com",
    "role": "operator"
  }'
```

### 사용자 정보 수정

```bash
curl -X PATCH http://localhost:8000/api/users/{user_id} \
  -H "Authorization: Bearer <admin_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "role": "viewer",
    "is_active": true,
    "email": "newemail@example.com"
  }'
```

### 비밀번호 리셋 (임시 재설정)

```bash
curl -X POST http://localhost:8000/api/users/{user_id}/password-reset \
  -H "Authorization: Bearer <admin_token>" \
  -H "Content-Type: application/json" \
  -d '{"new_password": "temp-password"}'
```

### 계정 잠금/해제

```bash
curl -X POST http://localhost:8000/api/users/{user_id}/lock \
  -H "Authorization: Bearer <admin_token>" \
  -H "Content-Type: application/json" \
  -d '{"is_locked": true}'
```

### 사용자 삭제 (Soft Delete)

```bash
curl -X DELETE http://localhost:8000/api/users/{user_id} \
  -H "Authorization: Bearer <admin_token>"
```

---

## 계정 보호 정책

### 자동 잠금

- 비밀번호 실패 횟수: **5회 연속 실패** → 계정 자동 잠금
- 잠금 해제: Admin만 `/users/{id}/lock` 엔드포인트로 해제
- 실패 횟수 초기화: 성공 로그인 또는 관리자 리셋

### 비밀번호 정책

- **최소 길이**: 8자 이상
- **해싱**: bcrypt (rounds=12)
- **저장**: 평문 불가, 해시만 저장
- **변경**: 비밀번호 리셋 엔드포인트 또는 개인 설정

---

## 감사 로그 (Audit Trail)

모든 인증 및 권한 변경 사항이 기록됩니다.

### 기록되는 액션

| Action Type | 설명 |
|------------|------|
| `LOGIN_SUCCESS` | 로그인 성공 |
| `LOGIN_FAILED` | 로그인 실패 |
| `LOGIN_LOGOUT` | 로그아웃 |
| `USER_CREATED` | 사용자 생성 |
| `USER_UPDATED` | 사용자 정보 수정 |
| `USER_DELETED` | 사용자 삭제 |
| `USER_LOCKED` | 계정 잠금 |
| `USER_UNLOCKED` | 계정 잠금 해제 |
| `PASSWORD_RESET` | 비밀번호 리셋 |
| `MFA_ENROLLED` | MFA 활성화 |
| `MFA_DISABLED` | MFA 비활성화 |

### 감사 로그 조회

```bash
curl -X GET http://localhost:8000/api/audit?action_type=LOGIN_SUCCESS \
  -H "Authorization: Bearer <admin_token>"
```

---

## 운영 가이드

### 초기 세팅

Admin 시드 사용자는 Alembic 마이그레이션 중 생성됩니다:

```bash
cd backend
export ADMIN_BOOTSTRAP_USERNAME="admin"
export ADMIN_BOOTSTRAP_PASSWORD="your-secure-password"
alembic upgrade head
```

### 사용자 추가

```bash
curl -X POST http://localhost:8000/api/users \
  -H "Authorization: Bearer <admin_token>" \
  -d '{
    "username": "operator1",
    "password": "initial-password",
    "email": "op1@example.com",
    "role": "operator"
  }'
```

### 계정 복구 (잠금 해제)

1. 사용자가 5회 실패 로그인 시 자동 잠금
2. Admin이 `/users/{id}/lock` 에서 `is_locked=false` 설정

```bash
curl -X POST http://localhost:8000/api/users/{user_id}/lock \
  -H "Authorization: Bearer <admin_token>" \
  -d '{"is_locked": false}'
```

### 비밀번호 분실

1. Admin이 `/users/{id}/password-reset` 호출하여 임시 비밀번호 설정
2. 사용자가 임시 비밀번호로 로그인 후 개인 설정에서 변경

---

## 환경변수

| 변수 | 설명 | 필수 | 예시 |
|-----|------|------|------|
| `ADMIN_BOOTSTRAP_USERNAME` | Admin 시드 사용자명 | No | admin |
| `ADMIN_BOOTSTRAP_PASSWORD` | Admin 시드 비밀번호 | Yes* | SecurePassword123 |
| `DASHBOARD_SECRET_KEY` | JWT 시크릿 키 | Yes | your-secret-key-min-32-chars |

\* 마이그레이션 중 admin 사용자 생성 시에만 필수. 미제공 시 스킵, 경고 출력.

---

## 보안 체크리스트

- [ ] ADMIN_BOOTSTRAP_PASSWORD 설정 (초기 admin 사용자 생성)
- [ ] DASHBOARD_SECRET_KEY 32자 이상 랜덤 문자열로 설정
- [ ] HTTPS 사용 (JWT 토큰 전송 시)
- [ ] 정기적 감사 로그 검토
- [ ] 사용하지 않는 계정 비활성화
- [ ] Admin 계정 MFA 활성화 권장
- [ ] Operator 이상 권한자 정기 재인증 권장

