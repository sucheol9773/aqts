---
from: lead
to: 2
subject: wal-archive-auto-chown
created: 2026-04-25T15:14:37Z
priority: P0  # [P0] 긴급, [Ask] 응답 요청, [FYI] 참고, [Lead-Approval] 리드 승인
---

# wal-archive-auto-chown

## 요약

OPS-028 P0 incident (`docs/operations/postgres-wal-archive-permission-2026-04-26.md`) 의 root cause #1 (19일간 WAL archive 권한 silent miss) 영구 해소를 위해 `docker-compose.yml` 의 postgres service 에 wal_archive 자동 chown 로직을 추가합니다.

## 맥락

### 사건 요약

2026-04-25 23:57 KST `AqtsDiskUsageCritical` 알림 (98.28%) 의 근본 원인 = `aqts_postgres_wal_archive` named volume 이 인프라 셋업 시점부터 `root:root drwxr-xr-x` 로 생성되어 postgres (`postgres` 사용자) 가 `cp pg_wal/<file> wal_archive/<file>` 실행 시 Permission denied. 19일간 archive_command 가 매분 실패하면서 `pg_wal/` 가 13.5GB 까지 누적 → 디스크 98%.

### 즉석 hotfix (영구 아님)

```bash
docker exec -u root aqts-postgres chown -R postgres:postgres /var/lib/postgresql/wal_archive
docker exec -u root aqts-postgres chmod 700 /var/lib/postgresql/wal_archive
```

위 hotfix 는 **현재 컨테이너 인스턴스에만 적용**. 서버 재프로비저닝, 볼륨 재생성, 또는 compose recreate 시 다시 root:root 로 돌아가는 회귀 가능. 본 메일은 영구 fix 위임.

### 왜 root:root 로 생성됐는가

Docker named volume 은 첫 마운트 시 호스트 root 가 디렉토리를 생성. postgres docker image 의 entrypoint 는 PGDATA 만 chown 하고 추가 마운트 (wal_archive) 는 건드리지 않음. compose 측에서 명시적으로 권한 설정 필요.

## 요청

`docker-compose.yml` 의 `postgres` service 변경. 다음 패턴 중 하나 (팀 2 판단):

### 옵션 A — entrypoint override

```yaml
postgres:
  image: timescale/timescaledb:2.14.2-pg16
  entrypoint:
    - /bin/bash
    - -c
    - |
      set -eu
      mkdir -p /var/lib/postgresql/wal_archive
      chown postgres:postgres /var/lib/postgresql/wal_archive
      chmod 700 /var/lib/postgresql/wal_archive
      exec docker-entrypoint.sh postgres "$@"
    - --
  command: ["postgres"]
  ...
```

장점: 매 컨테이너 시작 시 idempotent 하게 보장. 회귀 영구 차단.
단점: entrypoint override 가 baseline image 의 다른 entrypoint 동작을 덮어쓸 위험. 충분히 테스트 필요.

### 옵션 B — init container 패턴

별도 `postgres-init` 서비스를 `depends_on` 으로 postgres 앞에 두어 chown 만 수행한 뒤 종료:

```yaml
postgres-init:
  image: alpine:3.19
  volumes:
    - aqts_postgres_wal_archive:/wal_archive
  entrypoint:
    - sh
    - -c
    - 'chown 70:70 /wal_archive && chmod 700 /wal_archive'
  restart: "no"

postgres:
  ...
  depends_on:
    postgres-init:
      condition: service_completed_successfully
```

장점: postgres entrypoint 무수정. 책임 분리.
단점: 컨테이너 1개 추가 + uid 70 (postgres) 하드코딩 — `timescale/timescaledb:2.14.2-pg16` 의 postgres uid 는 70 으로 확인 필요.

### 옵션 C — Dockerfile 단계 (불채택 권장)

custom image 빌드. 단, 본 프로젝트는 timescaledb 공식 image 직접 사용 → custom image 도입은 공급망 보안 (cosign 서명 + grype scan) 부담 증가. 권장하지 않음.

### 리드 권장

**옵션 A** — entrypoint override. baseline `docker-entrypoint.sh` 가 `set -e` 로 PGDATA 권한을 보장하는 패턴이라 한 줄 추가만으로 일관성 유지. 옵션 B 는 "정의 ≠ 적용" wiring 한 단계 더 추가 → silent miss 가능성 (init container 가 fail 했는데 postgres 가 시작하는 race).

## 회귀 방어선

본 변경 후 다음을 검증:

```bash
# 컨테이너 stop + rm + up 으로 fresh 재생성
docker compose stop postgres && docker compose rm -f postgres && docker compose up -d postgres
sleep 20

# wal_archive 권한이 자동으로 postgres:postgres 인지 확인
docker exec aqts-postgres stat /var/lib/postgresql/wal_archive | grep -E 'Uid|Access'
# 기대: Uid: (70/postgres), Access: (0700/drwx------)

# 24시간 후 archive 가 정상 동작하는지
docker exec aqts-postgres psql -U aqts_user -d aqts -c 'SELECT archived_count, failed_count FROM pg_stat_archiver;'
# 기대: failed_count=0 또는 매우 낮은 수
```

## 게이트

- ruff/black 무영향 (yaml 만 변경)
- `docker compose config --quiet` 로 yaml 파싱 무결성 확인
- 위 회귀 방어선 통과
- `agent_docs/development-policies.md §14` Wiring Rule 의 "정의 ≠ 적용" 패턴 준수 — 변경된 entrypoint 가 실제로 적용되는지 컨테이너 stop+rm+up 후 재검증

## 응답 기한

**구현 머지**: 2026-04-29 (W1 마감 전후) 까지. P0 사건의 root cause 라 사이드 트랙으로 미루지 말 것 권장. 단, 단일 VM 환경의 compose 변경 = 운영 시스템 영향 → 야간 시간대 (한국 거래 외) 머지 권장.

**합의 응답**: 옵션 A vs B 선택만 우선 회신 (1-2일 내). 구현 PR 은 선택 후 진행.

## 참조

- `docs/operations/postgres-wal-archive-permission-2026-04-26.md` (OPS-028 회고 SSOT)
- `docker-compose.yml` 의 `postgres` service + `aqts_postgres_wal_archive` 볼륨 정의
- `agent_docs/development-policies.md §8` Silence Error 의심 원칙
- 자매 메일 — `agent_docs/mailboxes/team2/inbox/20260426-0014-pg-stat-archiver-alert.md` (모니터링 보강)
