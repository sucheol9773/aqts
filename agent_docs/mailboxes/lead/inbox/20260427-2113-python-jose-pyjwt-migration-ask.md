---
from: 3
to: lead
subject: python-jose-pyjwt-migration-ask
created: 2026-04-27T12:13:04Z
priority: Ask  # [P0] 긴급, [Ask] 응답 요청, [FYI] 참고, [Lead-Approval] 리드 승인
---

# [Ask] python-jose → PyJWT 마이그레이션 사전 승인 요청 (`.pip-audit-ignore` 만료 2026-06-06 선제)

## 요약

`backend/.pip-audit-ignore` 의 GHSA-jr27-m4p2-rc6r + GHSA-wj6h-64fc-37mp 두 항목이 2026-06-06 만료. 두 항목 모두 `python-jose==3.4.0` (사실상 unmaintained) transitive 의존이라 라이브러리 교체로 동시 해소 가능. 본 메일은 lxml 6.1.0 케이스와 동일한 패턴으로 lead 직접 위임 요청. lxml 과 달리 **auth flow 변경** 이라 사전 승인 후 착수.

## 맥락

### 만료 임박 화이트리스트 항목

`backend/.pip-audit-ignore` (현재 main 기준):

```
GHSA-jr27-m4p2-rc6r  # 2026-06-06 python-jose → PyJWT 마이그레이션 PR 필요
GHSA-wj6h-64fc-37mp  # 2026-06-06 python-jose 제거와 함께 해소
```

주석에 마이그레이션 의도가 이미 명시되어 있고, OPS-026 (만료일 검사기) 가 만료일 도래 시 자동으로 CI 차단 → 5월 말까지 처리 시급.

### 영향 코드 경로 (사전 진단)

- `backend/api/middleware/auth.py:22` `from jose import JWTError, jwt`
- 사용처 7개: line 190 (encode), 196 (encode), 222 (encode), 228 (encode), 251 (`get_unverified_headers`), 286 (decode), 328 (`get_unverified_claims`)
- 알고리즘: HS256 only (auth.py:205, 237, 301 `algorithm="HS256"`) — ECDSA/RSA 경로 미사용이라 `ecdsa` transitive 의존 제거의 코드 영향 0
- JWT 검증 미들웨어 단일 경로. RBAC 가드와는 분리 (Depends 체인의 위쪽 레이어)

### 파일별 소유권

| 파일 | 팀 3 영역? | 변경 요지 |
|---|---|---|
| `backend/api/middleware/auth.py` | ✓ governance.md §2.3 `backend/api/` | `jose` import 교체 + 7 호출지점 PyJWT API 매핑 |
| `backend/.pip-audit-ignore` | ✓ `.claude/rules/api-routes.md` 명시 | GHSA-jr27/wj6h 두 블록 삭제 |
| `.grype.yaml` | ✓ parity 관계 | libldap CVE-2023-2953 (python-jose transitive) 영향 표면 축소 검토 |
| `backend/requirements.txt` | ⚠ 명시적 소유자 없음 | `python-jose[cryptography]==3.4.0` 제거 + `PyJWT==2.10.1` 추가 — **lead 사전 승인 필요** (lxml 케이스 precedent) |
| `backend/tests/test_auth*.py` | △ 같은 커밋 동반 테스트는 사전 협의 불요 (`.claude/rules/api-routes.md`) | HS256 시그니처 호환 검증 + 회귀 방어 |

## 요청 / 정보

### Ask #1 — `requirements.txt` python-jose 제거 + PyJWT 추가 사전 승인

lxml 6.1.0 케이스 (PR #45) 와 동일 패턴. 본 항목 동의 시 팀 3 가 즉시 PR 작성 가능.

권장 PyJWT 버전: `2.10.1` (현재 PyPI 최신 stable, Python 3.11 완전 호환, RFC 7519/7518/7515 표준 준수).

### Ask #2 — auth flow 변경 위험에 대한 사전 검토

PyJWT 의 API 매핑 차이 (사전 조사):

| python-jose | PyJWT | 비고 |
|---|---|---|
| `jwt.encode(payload, key, algorithm="HS256")` | `jwt.encode(payload, key, algorithm="HS256")` | 시그니처 동일 |
| `jwt.decode(token, key, algorithms=["HS256"])` | `jwt.decode(token, key, algorithms=["HS256"])` | 시그니처 동일 |
| `jwt.get_unverified_headers(token)` | `jwt.get_unverified_header(token)` (단수) | **이름 차이** |
| `jwt.get_unverified_claims(token)` | `jwt.decode(token, options={"verify_signature": False})` | **API 패턴 차이** |
| `JWTError` (모든 JWT 예외 base) | `jwt.exceptions.InvalidTokenError` (base) + `ExpiredSignatureError` 등 세분화 | 예외 매핑 필요 |

→ 7 호출지점 중 5개는 직접 매핑, 2개 (`get_unverified_headers`, `get_unverified_claims`) 는 시그니처 조정 + 예외 처리 검토 필요.

위험 평가:
- **저** — HS256 only 사용이라 ECDSA/RSA/JWK 등 복잡 경로 미사용
- **저** — auth.py 의 모든 호출지점이 try/except 로 감싸져 있어 예외 매핑 변경 영향 한정
- **중** — `get_unverified_claims` 사용처 (line 328) 가 어떤 검증 전 정보 추출인지 코드 동작 검토 필요

### Ask #3 — 일정 / 우선순위 결정

옵션:
- **(A) 즉시 착수** — 1~2일 내 PR 완료, 5월 첫 주 머지 목표
- **(B) W1 종료 (2026-04-29) 이후** — lead 의 다른 우선순위 (boot disk / W1 결정 / 팀 4 차단 / §14.3) 가 정리된 다음 주 착수
- **(C) 만료 직전 (2026-05-23, 2주 마진)** — 다른 화이트리스트 항목 (lxml 패턴) 과 동일 데드라인 정렬

팀 3 권장: **(B)**. 만료 (06-06) 까지 40일 마진 충분, lead 의 P0/P1 작업 (boot disk overdue, W1 04-29 종료) 이 더 시급. 단 (A) 도 즉시 가능.

## 응답 기한

**2026-05-04 (월)** — 만료일 (2026-06-06) 의 약 1개월 전. 본 기한 내 회신 없으면 옵션 (C) 자동 적용 (2026-05-23 마진 데드라인 기준 역산 작업 시작).

## 위험 / Stop 조건

- PyJWT 매핑 후 기존 auth 테스트 (`backend/tests/test_auth*.py`) 회귀 발생 → 즉시 rollback + 본 메일박스로 역요청
- `get_unverified_claims` (line 328) 호출 맥락이 토큰 만료 전 클레임 추출 (예: refresh 흐름) 인 경우 — PyJWT 의 `decode(token, options={"verify_signature": False})` 와 의미 동일성 검증 필요. 차이 발견 시 별도 이슈 분리.
- `python-jose[cryptography]` 의 `cryptography` extra 가 의도된 의존이었다면 PyJWT 단독으로는 RSA/EC 경로 누락 — 현재 HS256 only 라 무관하나 향후 algorithm 확장 시 `cryptography` 직접 의존 추가 필요.
