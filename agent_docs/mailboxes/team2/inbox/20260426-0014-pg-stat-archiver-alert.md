---
from: lead
to: 2
subject: pg-stat-archiver-alert
created: 2026-04-25T15:14:37Z
priority: Ask  # [P0] 긴급, [Ask] 응답 요청, [FYI] 참고, [Lead-Approval] 리드 승인
---

# pg-stat-archiver-alert

## 요약

OPS-028 P0 incident 의 §4.1 monitoring 부재 (19일간 archive 실패가 silent miss 였음) 영구 해소를 위해 `pg_stat_archiver` 기반 prometheus 알림 신설을 위임합니다. 본 사건은 *증상* (디스크 98%) 만 alert 했고 *원인* (archive 실패) 은 detection 0 — 알림 레이어의 가장 큰 격차.

## 맥락

### 사건의 monitoring 격차

- `aqts_host_system` group (`monitoring/prometheus/rules/aqts_alerts.yml`) 에 `AqtsDiskUsageHigh`/`AqtsDiskUsageCritical` 가 있어 디스크 임계 초과는 잡힘
- 그러나 **archive 실패 자체** 를 보는 매트릭이 없어 19일간 매분 매시 `archive command failed` 가 docker logs 에 찍히는데도 alerting 0
- 결과: 원인은 19일 누적 silent miss, 증상은 마지막 수 시간만 visible
- 본 사건의 **재발 시점이 아니라 조기 감지** 가 본 메일의 목적

### 후보 매트릭

PostgreSQL 의 `pg_stat_archiver` 시스템 뷰가 다음 컬럼 노출:

| 컬럼 | 의미 | 알림 활용 |
|---|---|---|
| `archived_count` | 누적 archive 성공 횟수 | 증가 멈춤 = active issue |
| `last_archived_wal` | 마지막 archive 성공 WAL 파일명 | timeline tracking |
| `last_archived_time` | 마지막 archive 성공 시각 | **stale alert 핵심** — 이 값이 5분 이상 오래되면 issue |
| `failed_count` | 누적 archive 실패 횟수 | 증가율 > 0 = active issue |
| `last_failed_wal` | 마지막 실패 WAL | 어느 파일에서 막혔는지 |
| `last_failed_time` | 마지막 실패 시각 | issue 발생 시점 |

### Prometheus 노출 경로

postgres_exporter 가 표준 경로. 프로젝트 현 상태 확인 필요:

```bash
# 현재 prometheus targets 에 postgres exporter 있는지
curl -s http://localhost:9090/api/v1/targets | jq '.data.activeTargets[] | select(.labels.job | test("postgres|pg")) | .labels'
```

만약 미설치라면 본 메일에 postgres_exporter 추가도 포함.

## 요청

### 1. postgres_exporter 도입 (미설치 시)

`docker-compose.yml` 에 `prometheuscommunity/postgres-exporter:v0.15.0` 추가:

```yaml
postgres-exporter:
  image: prometheuscommunity/postgres-exporter:v0.15.0
  container_name: aqts-postgres-exporter
  restart: unless-stopped
  environment:
    DATA_SOURCE_NAME: "postgresql://${DB_USER}:${DB_PASSWORD}@postgres:5432/${DB_NAME}?sslmode=disable"
  ports:
    - "127.0.0.1:9187:9187"
  depends_on:
    postgres:
      condition: service_healthy
  networks:
    - aqts-network
```

`prometheus.yml.tmpl` 의 `scrape_configs` 에 job 추가:

```yaml
- job_name: aqts-postgres-exporter
  static_configs:
    - targets: ['postgres-exporter:9187']
      labels:
        host: "${HOST_LABEL}"
```

### 2. 신규 알림 group `aqts_postgres` (`monitoring/prometheus/rules/aqts_alerts.yml`)

```yaml
- name: aqts_postgres
  rules:
    - alert: AqtsPgArchiveFailing
      expr: rate(pg_stat_archiver_failed_count[5m]) > 0
      for: 2m
      labels:
        severity: critical
      annotations:
        summary: "PostgreSQL WAL archive 실패율 > 0 (host {{ $labels.host }})"
        description: |
          `pg_stat_archiver.failed_count` 이 5분 윈도우에서 증가 중. archive_command
          가 실패하고 있어 pg_wal 누적 → 디스크 압박 위험.
          OPS-028 회고 (`docs/operations/postgres-wal-archive-permission-2026-04-26.md`)
          §4.1 의 19일 silent miss 와 동일 패턴.
          런북: `docs/operations/postgres-wal-archive-permission-2026-04-26.md §5.1` (chown fix)

    - alert: AqtsPgArchiveStale
      expr: time() - pg_stat_archiver_last_archive_age_seconds > 600
      for: 5m
      labels:
        severity: warning
      annotations:
        summary: "PostgreSQL WAL archive 가 10분 이상 정체 (host {{ $labels.host }})"
        description: |
          `last_archived_time` 이 10분 이상 갱신되지 않음. archive_command 가 멈춰
          있을 가능성. critical 진입 전 조기 경보.

    - alert: AqtsPgWalSizeHigh
      expr: pg_settings_max_wal_size_mb > 0 and (pg_wal_size_bytes / 1024 / 1024) > (pg_settings_max_wal_size_mb * 4)
      for: 10m
      labels:
        severity: warning
      annotations:
        summary: "pg_wal 디렉토리 크기가 max_wal_size 의 4배 초과 (host {{ $labels.host }})"
        description: |
          정상 운영시 pg_wal ≤ 2 * max_wal_size. 4배 초과는 archive backlog
          누적 신호. OPS-028 사건 시 13.5GB / max_wal_size 1GB = 13.5배.
```

**주의**: `pg_wal_size_bytes` 와 `pg_settings_max_wal_size_mb` 매트릭명은 postgres_exporter 버전별로 다름 — 도입 후 `curl localhost:9187/metrics | grep -i wal` 로 실 매트릭명 확인하여 보정.

### 3. 회귀 방어선 — 본 알림이 작동하는지 합성 테스트

```bash
# 의도적으로 archive_command 를 실패시켜 알림 트리거 검증 (테스트 환경에서만)
docker exec -u root aqts-postgres chown root:root /var/lib/postgresql/wal_archive
sleep 300  # 5분 대기 — for: 2m + scrape interval
curl -s http://localhost:9093/api/v2/alerts | jq -r '.[] | select(.labels.alertname == "AqtsPgArchiveFailing") | .status.state'
# 기대: "active"

# 복구
docker exec -u root aqts-postgres chown postgres:postgres /var/lib/postgresql/wal_archive
```

**프로덕션 적용 금지** — 위 합성 테스트는 staging 또는 dev 환경에서만.

## 게이트

- ruff/black 무영향 (yaml 만)
- `promtool check rules monitoring/prometheus/rules/aqts_alerts.yml` 0 errors
- prometheus 재기동 후 `/api/v1/rules` 의 group count 가 1 증가 (기존 9 → 10)
- Doc Sync 워크플로 통과
- 합성 테스트 (staging) 통과 — `AqtsPgArchiveFailing` 이 의도적 fail 시 5분 내 트리거

## 응답 기한

**합의 응답**: 2026-04-29 (W1 마감 전후) — postgres_exporter 도입 여부 + 매트릭 명 검증 회신.

**구현 머지**: 2026-05-06 (ADR-002 Stage 2 Exit) 이후 — Pilot 측정 공정성 영향 회피. 단, 본 사건이 P0 였으므로 우선순위는 Pilot 측정 보다 높음 → W2 진입 (2026-04-29~) 시점 머지 권장.

## 참조

- `docs/operations/postgres-wal-archive-permission-2026-04-26.md` (OPS-028 회고 SSOT, §4.1 monitoring 격차)
- `monitoring/prometheus/rules/aqts_alerts.yml` (기존 9 group + `aqts_host_system` 패턴 원형)
- `docs/operations/cd-auto-prune-2026-04-16.md` (호스트 메트릭 도입 — node-exporter, 본 작업의 자매 case)
- `agent_docs/development-policies.md §14` Wiring Rule (정의 ≠ 적용 — 알림 규칙도 동일)
- 자매 메일 — `agent_docs/mailboxes/team2/inbox/20260426-0014-wal-archive-auto-chown.md` (root cause 영구 fix)
