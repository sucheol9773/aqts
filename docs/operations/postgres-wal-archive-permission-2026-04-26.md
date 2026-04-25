# PostgreSQL WAL archive 권한 silent miss 회고 (2026-04-26)

**문서 번호**: OPS-028
**분류**: 작업 기록 (P0 incident retrospective)
**소유**: 팀메이트 2 (인프라 / 모니터링)
**작성**: 리드 (긴급 대응 직후)
**최초 발견**: 2026-04-25 23:57 KST (Telegram CRITICAL 알림 — `AqtsDiskUsageCritical`, 98.28%)
**완전 복구**: 2026-04-26 00:09 KST 추정 (`/api/system/health: "healthy"` 회신)

---

## 1. 사건 요약

`aqts-server` (단일 GCP VM, 48GB boot disk) 의 루트 디스크가 98.28% 까지 차오르며 Alertmanager `AqtsDiskUsageCritical` 가 트리거. 진단 결과 **2026-04-06 인프라 셋업 시점부터 19일간 PostgreSQL WAL archive 가 권한 거부로 실패하던 silent miss** 가 누적되어 `pg_wal/` 디렉토리가 13.5GB 까지 비대화. archive 권한을 즉석에서 fix 한 후 catch-up 폭주로 disk 100% → postgres PANIC → mongodb docker network corruption → backend DNS resolution 실패까지 cascading 했다.

총 영향 시간 ≈ 35분 (Critical 알림 → 전체 복구). 데이터 손실 0 (pg_wal 은 보존, wal_archive 만 cleanup 으로 PITR 19일 윈도우 일부 손실).

---

## 2. 4단계 root cause chain

| # | 단계 | 트리거 | 결과 |
|---|---|---|---|
| 1 | **WAL archive 권한 silent miss (19일)** | `wal_archive` 볼륨이 `root:root drwxr-xr-x` 로 생성됨 (인프라 셋업 2026-04-06) | postgres (`postgres` 사용자) 가 archive cp 못 함, `pg_wal/` 누적 |
| 2 | **pg_wal 비대화 → 디스크 98%** | 19일간 873+ WAL segments 누적 (= 13.5GB) | `AqtsDiskUsageCritical` Alertmanager 트리거 |
| 3 | **Archive catch-up + 디스크 100%** | 권한 fix 직후 archive_command 폭주 catch-up (3.7GB / 7초) → wal_archive 가 자라면서 디스크 100% 도달 | postgres `PANIC: No space left on device` 크래시, restart loop |
| 4 | **mongodb docker network corruption** | disk full 기간 docker daemon 의 internal network metadata 손상 → mongodb 컨테이너 IP 가 `invalid IP` 로 표기 | backend DNS resolution `mongodb:27017` 실패. `Up 12 days (healthy)` 표기는 mongodb 내부 healthcheck (`mongosh --eval ping`) 라 외부 도달성과 무관해 silent |

---

## 3. 타임라인 (UTC+9, KST)

| 시각 | 이벤트 |
|---|---|
| 2026-04-06 03:03 | `wal_archive` 볼륨 root:root 로 생성 (인프라 셋업) — silent miss 시작 |
| 2026-04-25 23:31 | 가장 오래된 archive_command 실패 로그 확인 (실제 19일 내내 실패) |
| 2026-04-25 23:31~57 | `pg_wal/` 13.5GB 까지 누적, 루트 디스크 98% 도달 |
| 2026-04-25 23:57 | Telegram `AqtsDiskUsageCritical` 알림 발송 |
| 2026-04-25 23:58 | 리드 진단 시작 (`docs/operations/cd-auto-prune-2026-04-16.md §6.2` 런북 참조) |
| 2026-04-26 00:00 추정 | 진단 도중 disk 자체 회복 76% (원인 미확인 — postgres 의 emergency CHECKPOINT 일부 효과 또는 다른 cleanup) |
| 2026-04-26 00:00 | postgres logs 에서 `archive command failed: Permission denied` 19일 누적 발견 — root cause 확정 |
| 2026-04-26 00:01 | `chown postgres:postgres /var/lib/postgresql/wal_archive` + `chmod 700` 적용. archive 즉시 catch-up 시작 (7초 사이 7개 WAL = 3.7GB 복사) |
| 2026-04-26 00:04 | Catch-up 폭주로 disk 100% 도달, postgres `PANIC: pg_wal/xlogtemp.* No space left` 크래시, restart loop 진입 |
| 2026-04-26 00:05 | wal_archive 12GB / 755 files 식별. `sudo rm -rf .../*` 시도 → glob 권한 문제로 실 삭제 실패 (silent) |
| 2026-04-26 00:06 | `sudo bash -c 'rm -rf .../*'` 로 wal_archive 정리 성공 (12GB → 40K). disk 95% 회복 |
| 2026-04-26 00:07 | postgres `docker compose up -d postgres` 성공. recovery 완료, CHECKPOINT 이 723 WAL 회수, `pg_wal` 13.5GB → 2.3GB |
| 2026-04-26 00:08 | backend `Up` 그러나 `/health: degraded — mongodb DNS resolution failed` |
| 2026-04-26 00:09 | mongodb network IP `invalid IP` 식별 → mongodb 컨테이너 stop+rm+up 으로 IP 172.18.0.10 재할당 |
| 2026-04-26 00:09 | backend restart 후 `/health: healthy` — 전체 복구 |

---

## 4. Silent miss 의 원인 분석

### 4.1 왜 19일간 발견 안 됐는가

`AqtsDiskUsageCritical` (≥90%) 알림은 **증상** (디스크 사용률) 만 감시했고 **원인** (archive_command 실패 카운트) 은 monitoring 매트릭에 없었다. 다음 모니터링 매트릭이 부재했다:

- `pg_stat_archiver{failed_count}` — archive 실패 누적 카운트
- `pg_stat_archiver{last_failed_time}` — 마지막 실패 시각 (active issue 여부)
- `pg_wal_size_bytes` (or `archive_pending_count`) — pg_wal 디렉토리 비대화

postgres 가 `LOG: archive command failed` 를 stderr 로 매분 출력했으나 이를 alerting 으로 승격하는 경로가 없었다 (loguru/structured logging 기반 backend 와 달리 postgres docker logs 는 prometheus 가 scrape 하지 않음).

### 4.2 왜 healthcheck 가 통과했는가

`docker-compose.yml` 의 mongodb healthcheck 는 `mongosh --eval 'db.runCommand({ping:1})'` — **컨테이너 내부에서 자기 자신 ping**. 외부 docker network 도달성과 무관. 따라서:

- mongodb 의 docker network IP 가 `invalid IP` 로 손상됐을 때도 healthcheck 는 통과
- `Up 12 days (healthy)` 표기가 외부에서 보면 정상으로 보였지만 실제로 backend 에서 `mongodb:27017` 가 resolution 실패

### 4.3 왜 boot disk scope 가 부족했는가

`gcloud compute disks resize` 가 VM 안에서 `Request had insufficient authentication scopes` 로 실패. VM 의 service account 에 `https://www.googleapis.com/auth/compute` scope 가 없어 disk admin 작업 불가. 긴급 boot disk 증설 경로가 차단됨 (Mac 측 로컬 gcloud 또는 GCP console UI 가 우회 경로).

---

## 5. 즉석 조치 + 회복 절차

### 5.1 권한 fix (표준 절차)

```bash
docker exec -u root aqts-postgres chown -R postgres:postgres /var/lib/postgresql/wal_archive
docker exec -u root aqts-postgres chmod 700 /var/lib/postgresql/wal_archive
```

### 5.2 wal_archive cleanup (12GB 회수, glob 권한 함정 회피)

```bash
# ✗ 작동 안 함 — user shell 이 /var/lib/docker/volumes/ 못 읽어 glob 빈 매치
sudo rm -rf /var/lib/docker/volumes/aqts_postgres_wal_archive/_data/*

# ✓ 작동 — sudo 안에서 glob expansion
sudo bash -c 'rm -rf /var/lib/docker/volumes/aqts_postgres_wal_archive/_data/*'
```

### 5.3 mongodb network corruption 회복

```bash
docker compose stop mongodb
docker compose rm -f mongodb
docker compose up -d mongodb
sleep 25
# 재할당 IP 확인
docker inspect aqts-mongodb --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}(IP={{$v.IPAddress}}){{end}}'
# backend 도 재기동해서 새 IP 로 재연결
docker compose restart backend
```

mongodb volume 은 `aqts_mongodb_data` named volume 이라 컨테이너 재생성해도 데이터 보존.

---

## 6. 후속 작업 (3건 위임)

### 6.1 [팀 2] docker-compose wal_archive 자동 chown

**목적**: §4.1 의 root cause (권한 silent miss) 재발 방지. compose 의 postgres entrypoint 또는 init container 에 `chown postgres:postgres /var/lib/postgresql/wal_archive` 자동화.

**메일**: `agent_docs/mailboxes/team2/inbox/20260426-0035-wal-archive-auto-chown.md`

### 6.2 [팀 2] pg_stat_archiver 알림 신설

**목적**: §4.1 의 monitoring 부재 해소. archive 실패 count 가 1 이상이면 critical 알림.

**메일**: `agent_docs/mailboxes/team2/inbox/20260426-0035-pg-stat-archiver-alert.md`

### 6.3 [리드 self] boot disk 48GB → 100GB 증설

**목적**: §4.3 의 emergency 경로 부재 해소 + archive catch-up 시 디스크 마진. 본 사건은 wal_archive 정리로 임시 회복했으나 archive 가 다시 catch-up 하면 12GB 가 재누적될 수 있음. 영구 마진 확보.

**메일**: `agent_docs/mailboxes/lead/inbox/20260426-0035-boot-disk-resize-todo.md`

---

## 7. 미해결 / 추가 조사 필요

- **알림 시점 ~ 진단 시작 사이 11GB 자동 drop** — Alertmanager 알림 본문 98.28% (47.2GB used) 이었으나 `df -h /` 첫 진단 때 76% (36GB used). 약 11GB 가 사이에 줄어듦. 가능 원인: postgres 의 emergency CHECKPOINT, OOM-induced 일부 프로세스 종료, 또는 다른 자동 cleanup. 본 사건의 cascade 분석에 영향 없으나 silent recovery 경로 식별을 위해 audit log 검토 권장.
- **archive_command 가 매분 매시 실패한 19일간의 postgres docker logs 가 rotate 됐을 가능성** — docker logs 기본 retention 이 짧으면 long-term audit 어려움. log driver 설정 검토 필요.

---

## 8. 학습된 패턴

1. **Healthcheck 의 의미 재정의** — 컨테이너 내부 healthcheck 는 *프로세스 생존* 만 확인. 외부 reachability 는 별도 probe (e.g., 다른 컨테이너에서 `getent hosts <name>`) 또는 application-level health 가 필요.
2. **Silent miss 검출 4축** — 본 사건은 (a) 권한 silent miss + (b) monitoring 부재 + (c) healthcheck mis-coverage + (d) emergency scope 부재 가 동시에 작용. 각 축 독립적 방어선 필요.
3. **Catch-up 폭주의 위험** — 19일 누적 backlog 를 즉석 fix 후 풀어주면 catch-up 자체가 새 incident 를 만들 수 있음. fix 시점에 disk margin 사전 확보 또는 catch-up 속도 제한 (e.g., archive_command 에 `nice`/`ionice`) 고려.
4. **`sudo rm -rf .../*` 의 glob 권한 함정** — user shell 이 sudo 외부에서 glob expansion 을 시도. 디렉토리 read 권한이 없으면 silent empty match. 항상 `sudo bash -c 'rm -rf .../*'` 패턴 사용.

---

## 9. 참조

- `docs/operations/cd-auto-prune-2026-04-16.md` (OPS — node-exporter + 디스크 알림 + CD 자동 prune. 본 사건의 1차 대응 런북)
- `docker-compose.yml` (`aqts_postgres_wal_archive` 볼륨 정의)
- `monitoring/prometheus/rules/aqts_alerts.yml` (`aqts_host_system` group — 본 사건 트리거 알림)
- `agent_docs/development-policies.md §8` "Silence Error 의심 원칙" (본 사건의 19일 silent miss 가 §8 의 직접 사례)
- `agent_docs/development-policies.md §15` "SSH heredoc stdin 격리" (본 사건의 ssh-and-exec 방식이 §15 동선)
