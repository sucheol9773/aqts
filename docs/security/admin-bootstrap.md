# Admin 사용자 부트스트랩 운영 절차

## 1. 배경

본 시스템의 RBAC(`docs/security/rbac-policy.md`) 는 admin / operator / viewer
세 역할을 두며, 사용자 생성·역할 변경·MFA 강제 같은 모든 admin 전용 라우트는
admin 역할 사용자만 호출할 수 있다. 따라서 운영 DB 에 **admin 계정이 한
명도 없는 상태** 에서는 시스템이 사실상 자기 자신을 관리할 수 없다.

마이그레이션 002 (`backend/alembic/versions/002_rbac_users.py`) 에는
`ADMIN_BOOTSTRAP_PASSWORD` 환경변수가 설정되어 있을 때 admin 사용자를
자동 시드하는 경로가 있으나, 이 경로는 **마이그레이션이 처음 적용되는
시점에만** 동작한다. 즉 마이그레이션이 이미 적용된 뒤에는 무력화되며,
`alembic stamp` 로 baseline 을 마킹한 환경(=init_db.sql 부트스트랩 후 stamp
복구) 에서는 002 의 시드 경로 자체가 한 번도 실행되지 않는다.

본 문서는 이 공백을 메우는 일회성 admin 부트스트랩 절차를 정의한다.

## 2. 운영 정책

- **CD 파이프라인은 `ADMIN_BOOTSTRAP_PASSWORD` 를 알지 못한다.**
  비밀번호는 본 절차를 실행하는 운영자의 셸 환경에만 일회성으로 주입된다.
  GitHub Actions secret 으로 흐르지 않으며, 어떤 컨테이너의
  영속 환경변수에도 등록되지 않는다.
- **본 절차는 환경 lifetime 당 1회만 실행한다.** 평상시 운영(코드 배포,
  마이그레이션 추가, 백엔드/스케줄러 재시작) 에서는 절대 재실행하지
  않는다. admin 계정은 `users` 테이블에 영속화되며, 한 번 생성된 뒤에는
  배포·재시작과 무관하게 계속 존재한다.
- **재실행이 필요한 경우** 는 다음 두 가지로 한정된다.
  1. 운영 DB 를 완전히 새로 부트스트랩 (신규 region, DR 복제 후 첫 기동, 데이터 wipe 후 재구축)
  2. 실수로 admin 계정을 모두 삭제하여 admin 이 한 명도 없는 상태가 된 경우
- **본 CLI 는 멱등하다.** admin 역할 사용자가 1명 이상 존재하면 아무
  변경 없이 종료 코드 0 으로 종료한다. 동일 환경에서 실수로 두 번
  실행해도 부작용이 없다.
- **생성 직후 비밀번호를 즉시 회전한다.** 운영자는 첫 로그인 후 곧바로
  `/api/users/me` (또는 비밀번호 변경 라우트) 로 비밀번호를 새로 설정한다.
  본 CLI 에 사용된 비밀번호는 일회성 토큰으로 간주한다.

## 3. 비밀번호 정책

본 CLI 가 강제하는 부트스트랩 비밀번호 최소 요건:

- 최소 12자
- 영문 / 숫자 / 특수문자 중 최소 2종류 이상 포함

운영자는 부트스트랩 시점부터 위 정책을 만족하는 비밀번호를 사용해야
하며, 회전 후 사용자가 변경하는 비밀번호는 별도의 운영 정책에 따른다.

## 4. 사전 조건

다음 두 가지가 만족되어야 본 절차를 실행할 수 있다.

1. `alembic upgrade head` 가 002 이상까지 적용되어 `roles` 테이블에
   `admin` 행이 존재해야 한다. CD 파이프라인의 5b 단계가 정상 동작했다면
   본 조건은 자동으로 충족된다.
2. 운영 서버에서 `aqts-postgres` 컨테이너가 정상 기동 중이고, 백엔드
   이미지(`ghcr.io/${ORG}/aqts-backend:<tag>`) 가 cosign 서명 검증을
   통과한 상태로 pull 되어 있어야 한다.

## 5. 실행 절차 (서버 SSH 후)

### 5.1 네트워크/이미지 식별

```bash
POSTGRES_NET=$(docker inspect aqts-postgres \
  --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}')
IMAGE_REF=$(docker inspect aqts-backend --format '{{.Config.Image}}')
echo "network: $POSTGRES_NET"
echo "image:   $IMAGE_REF"
```

### 5.2 비밀번호 일회성 입력 (셸 히스토리에 남지 않도록)

```bash
read -s -p "ADMIN_BOOTSTRAP_PASSWORD: " ADMIN_BOOTSTRAP_PASSWORD
echo
export ADMIN_BOOTSTRAP_PASSWORD
# 선택: 별도 username 사용 시
# export ADMIN_BOOTSTRAP_USERNAME="ops_admin"
```

### 5.3 일회성 컨테이너로 CLI 실행

```bash
docker run --rm \
  --network "$POSTGRES_NET" \
  --env-file ~/aqts/.env \
  -e ADMIN_BOOTSTRAP_USERNAME \
  -e ADMIN_BOOTSTRAP_PASSWORD \
  "$IMAGE_REF" \
  python -m scripts.create_admin
```

성공 시 출력 예시:

```
INFO create_admin: admin 사용자 생성 완료: username=admin id=<uuid>
INFO create_admin: admin 부트스트랩 완료. 즉시 비밀번호를 회전하라 (/api/users/me).
```

이미 admin 이 존재하는 환경에서 재실행한 경우(멱등):

```
INFO create_admin: admin 역할 사용자가 이미 1명 이상 존재한다. 멱등 종료 (변경 없음).
```

### 5.4 환경변수 즉시 제거

```bash
unset ADMIN_BOOTSTRAP_PASSWORD
unset ADMIN_BOOTSTRAP_USERNAME
history -d $(history 1)  # 직전 명령 히스토리 삭제 (셸 설정에 따라)
```

### 5.5 비밀번호 회전

운영자 PC 의 브라우저나 API 클라이언트에서 위 username/비밀번호로 로그인한
직후, 비밀번호 변경 라우트로 새 비밀번호를 설정한다. 부트스트랩에 사용한
비밀번호는 더 이상 어디에도 사용하지 않는다.

## 6. 검증

```bash
docker exec -i aqts-postgres bash -c '
psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "
SELECT u.username, r.name
  FROM users u JOIN roles r ON u.role_id = r.id
 WHERE r.name = '"'"'admin'"'"';
"'
```

생성된 admin 사용자명이 출력되어야 한다.

## 7. 종료 코드

| 코드 | 의미 |
| --- | --- |
| 0 | 성공 또는 이미 존재 (멱등) |
| 1 | 환경변수 부재 / 비밀번호 정책 위반 / DB 오류 / 마이그레이션 미적용 |

## 8. 단위테스트

본 CLI 의 검증 로직은 `backend/tests/test_create_admin.py` 에서
DB 의존성을 모킹하여 검증된다. 검증 항목:

- 비밀번호 정책 (길이/문자 종류/경계값)
- 환경변수 누락/공백 처리, 기본 username
- admin role 동적 조회 (id 하드코딩 금지 — 마이그레이션 INSERT 순서가
  바뀌어도 안전)
- 멱등성 (admin 이 이미 존재하면 INSERT 미실행)
- username 중복 차단 (다른 역할로 동일 이름이 존재할 때)
- 정상 생성 시 `AuthService.hash_password` 사용 + `role_id` 매핑

## 9. 보안 주의사항

- 본 CLI 가 사용하는 비밀번호는 셸 환경변수에만 일시적으로 존재한다.
  파일/secret store 에 저장하지 않는다.
- `--env-file ~/aqts/.env` 는 DB 접속 정보 등 운영 환경 설정을 전달하기
  위함이며, 그 안에 `ADMIN_BOOTSTRAP_PASSWORD` 를 적어두지 않는다.
- 본 절차는 감사 로그 대상이다. 실행 시점·운영자·서버 호스트명을
  운영 일지에 기록한다.
