# 인프라 backfill audit — §14.5 invariant 1회 검증 — 2026-04-27

> **문서 번호**: OPS-030
>
> **목적**: OPS-029 (`infrastructure-setup-checklist-2026-04-26.md`) 의 §14.5 invariant 체크리스트 신설 *이전* 에 만들어진 모든 인프라가 invariant 를 충족하는지 1회 audit. 미충족 항목은 본 문서에 기록하고 별도 fix PR 시리즈로 위임. 자매 메일 `agent_docs/mailboxes/team2/processed/20260426-0028-infrastructure-setup-discipline.md` 축 2 산출물.

---

## 0. 요약

| 카테고리 | 대상 수 | PASS | GAP / FAIL | 미검증 (server) |
|---|---|---|---|---|
| Docker named volume | 8 | 1 | 0 | 7 |
| Prometheus scrape job | 6 | 6 | 0 | 0 (정적 분석 한정) |
| Alertmanager receiver | 3 | 3 | 0 | 0 (정적 분석 한정) |
| Service monitoring 격차 | 13 services | 5 | 8 | 0 |

**갱신 이력**:
- 2026-04-27 P1 해소 — prometheus self-scrape 추가 (`feat/prometheus-self-scrape` PR). §0 의 scrape job 5→6, monitoring 격차 PASS 4→5 / GAP 9→8 로 갱신.

**TOP 발견** (2026-04-27 시점):

1. **8 services 가 prometheus scrape 안 됨** (mongodb, redis, scheduler, db-backup, grafana, otel-collector, jaeger, alertmanager). prometheus 자체는 P1 해소. 일부는 의도된 누락이나 (jaeger UI 자체 / otel-collector 는 push 모드), 일부는 §14.5 의 "continuous monitoring" invariant 미충족.
2. **Volume 7개 의 owner / mode runtime 검증 필요** — postgres_wal_archive 는 PR #59 entrypoint override 로 자동 fix 적용. 나머지 (postgres_data, mongodb_data, redis_data, backup_data, prometheus_data, alertmanager_data, grafana_data) 는 서버 측 `docker exec stat` 으로 1회 검증 필요.
3. **alertmanager 합성 테스트 부재** — 3 receiver (critical/warning/info) 모두 정의되어 있으나 실제 telegram 발송 합성 테스트 기록이 없음. OPS-031 (예정) 에서 합성 테스트 절차 신설 권장.

---

## 1. Docker named volume audit (8 volumes)

`docker-compose.yml:479-487` 의 `volumes:` 블록 + 사용 서비스 매핑 (compose 파일 정독으로 추출).

| Volume | 사용 서비스 | mount-point | 기대 owner | §14.5 setup-time | continuous monitoring | self-documenting |
|---|---|---|---|---|---|---|
| `postgres_data` | postgres | `/var/lib/postgresql/data` | postgres (uid 70) | 🔍 server check (image entrypoint 가 PGDATA chown) | ✗ pg_database_size 매트릭 미노출 | ⚠️ docker-entrypoint.sh 자동 동작 — 본 문서 외 명시 없음 |
| `postgres_wal_archive` | postgres + db-backup (ro) | `/var/lib/postgresql/wal_archive` | postgres (uid 70) | ✓ **PASS** (PR #59 entrypoint override) | ✓ pg_stat_archiver 매트릭 (PR #60) | ✓ OPS-028 retro |
| `mongodb_data` | mongodb | `/data/db` | mongodb (uid 999) | 🔍 server check | ✗ mongodb_exporter 미설치 | ⚠️ image 자동 — 명시 없음 |
| `redis_data` | redis | `/data` | redis (uid 999) | 🔍 server check | ✗ redis_exporter 미설치 | ⚠️ image 자동 — 명시 없음 |
| `backup_data` | db-backup + (postgres_wal_archive ro) | `/backups` | (db-backup 컨테이너 root?) | 🔍 server check (uid 검증 + 백업 cron 실제 write 검증) | ✗ 백업 파일 크기 / 마지막 성공 시각 매트릭 부재 | ⚠️ scripts/backup_cron.sh 의 묵시적 동작 |
| `prometheus_data` | prometheus | `/prometheus` | nobody (uid 65534) 또는 prometheus | 🔍 server check | N/A (자체 매트릭) | ⚠️ image 자동 |
| `alertmanager_data` | alertmanager | `/alertmanager` | nobody (uid 65534) 또는 alertmanager | 🔍 server check | N/A | ⚠️ image 자동 |
| `grafana_data` | grafana | `/var/lib/grafana` | grafana (uid 472) | 🔍 server check | N/A | ⚠️ image 자동 |

**상세 판정**:

### 1.1 `postgres_wal_archive` ✓ **PASS** (유일)

- **setup-time**: PR #59 (`c3855ea`) entrypoint override 가 매 컨테이너 시작 시 `chown postgres:postgres + chmod 700` idempotent 보정. fresh 프로비저닝 / 볼륨 재생성 / compose recreate 모든 경로에서 자동.
- **continuous monitoring**: PR #60 (`9093103`) postgres-exporter + `aqts_postgres` 알림 그룹 (`AqtsPgArchiveFailing`, `AqtsPgArchiveStale`, `AqtsPgExporterMissing`).
- **self-documenting**: OPS-028 incident retro + OPS-029 §2.A 카테고리 + 본 문서.

### 1.2 GAP — anonymous volume 4개 (mail 에서 언급)

자매 메일 §축2-1 에 anonymous volume 4개 (`436d809b...`, `a5e8e0f0...`, `a9b59358...`, `c90fbe28...`) 가 docker prune 대상 검토라고 적혀 있음. 본 audit 시점에 origin/main 의 compose 정독으로는 anonymous volume 확인 불가 (서버 측 `docker volume ls` 로만 확인). server check 로 위임.

### 1.3 GAP — runtime 매트릭 부재 (3 services)

`mongodb_data`, `redis_data`, `backup_data` 의 사용 컨테이너가 prometheus 스크랩 대상이 아니라 §14.5 의 "continuous monitoring" invariant 미충족. 후속 작업:

- **mongodb-exporter 도입** — `percona/mongodb_exporter` 또는 `bitnami/mongodb-exporter`. 신규 PR.
- **redis-exporter 도입** — `oliver006/redis_exporter`. 신규 PR.
- **backup_data 자체 매트릭** — backup_cron.sh 가 `pg_stat_archiver_*` 외에 자체 매트릭을 노출하지 않음. 백업 성공/실패 횟수 + 마지막 성공 시각을 prometheus textfile collector (node-exporter `--collector.textfile.directory`) 또는 backup_cron.sh 가 직접 push 하는 방식 검토.

---

## 2. Prometheus scrape job audit (6 jobs)

`monitoring/prometheus/prometheus.yml.tmpl` 정독.

| Job | Target | 실측 (정적) | 매트릭 expose 검증 | up=1 검증 |
|---|---|---|---|---|
| `aqts-backend` | backend:8000 | ✓ FastAPI `/metrics` (prometheus_client) | ✓ `aqts_http_*`, `aqts_*_total` 등 다수 | 🔍 server `curl /api/v1/targets` |
| `aqts-backend-stable` | backend-stable:8000 | ✓ canary 미사용 시 연결 실패 무시 (compose 주석 명시) | (canary 환경에서만) | 🔍 server check (canary on 시) |
| `aqts-backend-canary` | backend-canary:8000 | ✓ canary 미사용 시 연결 실패 무시 | (canary 환경에서만) | 🔍 server check (canary on 시) |
| `aqts-node-exporter` | node-exporter:9100 | ✓ host 라벨 sed 치환 | ✓ `node_filesystem_*`, `node_cpu_*` | 🔍 server check |
| `aqts-postgres-exporter` | postgres-exporter:9187 | ✓ PR #60 신규 도입 | ✓ `pg_stat_archiver_*` (예상) | 🔍 server check (PR #60 deploy 후) |
| `aqts-prometheus` | prometheus:9090 | ✓ self-scrape (P1 해소, 2026-04-27) | ✓ `prometheus_config_last_reload_successful`, `prometheus_rule_evaluation_*`, `prometheus_tsdb_*` | 🔍 server check |

**모든 6 job 이 정적 분석 PASS**. 실측 PASS 여부는 `curl http://localhost:9090/api/v1/targets | jq` 출력 (server 측) 으로 확인 필요.

### 2.1 GAP — 8 services 미스크랩 (2026-04-27 prometheus self-scrape 해소 후)

다음 서비스가 prometheus scrape job 으로 등록되지 않음:

| Service | 매트릭 노출 가능? | 의도된 누락? | 후속 |
|---|---|---|---|
| `mongodb` | mongodb-exporter 필요 | ✗ unintended | §1.3 후속 PR |
| `redis` | redis-exporter 필요 | ✗ unintended | §1.3 후속 PR |
| `scheduler` | FastAPI 아님 (background loop) | △ 부분 의도 — heartbeat 매트릭 추가 가능 | 후속 검토 |
| `db-backup` | cron 컨테이너 — push 매트릭 필요 | △ 부분 의도 | §1.3 후속 |
| `grafana` | `/metrics` 자체 노출 | △ 의도된 누락 가능 | 검토 |
| `otel-collector` | 13133 health check / 8888 prometheus internal | △ 의도된 누락 — push 모드 | 검토 |
| `jaeger` | jaeger UI 자체 | ✓ 의도된 — 자체 UI 로 관측 | OK |
| `alertmanager` | `/metrics` 노출 | △ 의도된 누락 가능 | 검토 |
| ~~`prometheus` (self-scrape)~~ | self-monitoring 표준 | ~~✗ unintended~~ → ✓ **RESOLVED** | ✓ P1 해소 (PR feat/prometheus-self-scrape, 2026-04-27) |

**우선순위 후속**: ~~prometheus self-scrape~~ (P1 해소) > mongodb_exporter > redis_exporter > scheduler heartbeat 매트릭.

---

## 3. Alertmanager receiver audit (3 receivers)

`monitoring/alertmanager/alertmanager.yml.tmpl` 정독.

| Receiver | 채널 | severity 매핑 | 환경변수 sed 치환 | 합성 테스트 기록 |
|---|---|---|---|---|
| `telegram-critical` | Telegram (HTML, 1m group, 1h repeat) | critical | `${TELEGRAM_BOT_TOKEN}`, `${TELEGRAM_CHAT_ID}` | ✗ 부재 |
| `telegram-warning` | Telegram (HTML, 5m group, 4h repeat) | warning | 동일 | ✗ 부재 |
| `telegram-info` | Telegram (HTML, 15m group, 12h repeat) | info | 동일 | ✗ 부재 |

**모든 receiver 가 정적 분석 PASS**. inhibition 규칙도 정합 (BackendDown → 하위 알림 억제, critical → 동일 alertname warning 억제).

### 3.1 GAP — 합성 테스트 기록 부재

§14.5 invariant 의 "continuous + self-documenting" 충족을 위해 각 severity 별 합성 테스트 1회 실행 + 결과 기록 필요. 절차 후보:

```bash
# critical 합성 테스트 (staging only)
amtool alert add critical_synthetic alertname=SyntheticCritical severity=critical \
  --alertmanager.url=http://localhost:9093
# 30초 ~ 1분 내 telegram 채널 수신 확인
```

후속: `docs/operations/alertmanager-synthetic-test-procedure.md` (예상 OPS-031) 신설 + 분기 1회 실행 cadence 결정.

---

## 4. 13 services 의 §14.5 self-documenting 충족 여부

§14.5 의 "self-documenting" invariant — 각 인프라 작업이 PR 본문 또는 OPS 회고 문서에 검증 결과를 quote 했는가.

| Service | 도입 시점 | self-documenting | 평가 |
|---|---|---|---|
| postgres | 초기 | ⚠️ docker-entrypoint.sh 자동 — PR/OPS 명시 부재. PR #59 가 wal_archive 에 한정해 보강. | GAP |
| postgres-exporter | PR #60 (2026-04-26) | ✓ PR #60 본문 + OPS-028 §4.1 | PASS |
| mongodb | 초기 | ⚠️ 동일 | GAP |
| redis | 초기 | ⚠️ 동일 | GAP |
| backend / scheduler | 초기 | ✓ docs/architecture, docs/PRD | PASS |
| prometheus | 초기 | ✓ OPS-016 (cd-auto-prune), OPS-028 §4 | PASS |
| alertmanager | 초기 | ✓ docs/operations/alerting-audit-2026-04, alert-pipeline-runbook | PASS |
| db-backup | 초기 | ⚠️ scripts/backup_cron.sh 묵시적 동작, 별도 OPS 미존재 | GAP |
| grafana | 초기 | ⚠️ provisioning 파일은 있으나 셋업 회고 미존재 | GAP |
| otel-collector | OPS-016 | ✓ OPS-016 | PASS |
| jaeger | OPS-016 | ✓ OPS-016 | PASS |
| node-exporter | OPS-016 (cd-auto-prune-2026-04-16) | ✓ OPS-016 | PASS |

**4 서비스 GAP** (postgres / mongodb / redis / backup): 초기 셋업 시 OPS 회고 부재. 본 audit (OPS-030) 가 일부 보강.

---

## 5. 후속 작업 제안 (Fix PR series)

본 audit 의 GAP 항목별 후속 작업 매핑:

| 우선 | 작업 | OPS | 영역 |
|---|---|---|---|
| P1 | volume 7개 server-side runtime 검증 (`docker exec stat`) | (audit 본 문서 보강) | 사용자 측 deploy 시점 |
| ~~P1~~ | ~~prometheus self-scrape job 추가~~ | ✓ 해소 2026-04-27 | 팀 2 — PR `feat/prometheus-self-scrape` |
| P2 | mongodb_exporter 도입 + scrape job + 알림 group | TBD | 팀 2 — compose + monitoring + rules |
| P2 | redis_exporter 도입 + scrape job + 알림 group | TBD | 팀 2 — compose + monitoring + rules |
| P2 | backup_cron 매트릭 (textfile collector) + 알림 | TBD | 팀 2 — scripts + monitoring |
| P3 | scheduler heartbeat 매트릭 노출 | TBD | 팀 2 — `backend/core/scheduler*` (팀 2 영역) |
| P3 | alertmanager 합성 테스트 절차 | OPS-031 | 팀 2 — docs/operations |
| P3 | grafana 셋업 회고 (provisioning + dashboard) | TBD | 팀 2 — docs/operations |

**전체 fix series 의 deadline 권장**: 2026-05-13 (mail 의 축 2 deadline) 이전에 P1 완료, P2 는 2026-05-20 까지 1건 이상 머지 (mongodb_exporter 또는 redis_exporter 우선).

---

## 6. self-application

본 audit 작성이 §14.5 의 invariant 를 만족하는가:

- **setup-time**: 본 PR 자체는 인프라 작업이 아니라 docs-only — N/A.
- **continuous monitoring**: 본 audit 결과의 follow-up 작업 (§5) 이 진행됨에 따라 audit 본문이 stale 해질 위험. → 후속 작업이 머지될 때마다 §1~§4 표의 해당 row 를 갱신해야 함.
- **self-documenting**: 본 문서 자체가 self-documenting 산출물.

self-application 재검증 권장: 2026-05-13 (P1 완료 시점), 2026-05-20 (P2 1건 이상 완료 시점). 매 시점에 본 §0 요약 표 + §1~§4 표의 현재 상태 갱신.

---

## 7. 관련 문서

- `agent_docs/development-policies.md §14.5` — 운영 Wiring Rule (본 audit 의 invariant SSOT)
- `docs/operations/infrastructure-setup-checklist-2026-04-26.md` (OPS-029) — 본 audit 의 체크리스트 SSOT
- `docs/operations/postgres-wal-archive-permission-2026-04-26.md` (OPS-028) — 본 정책 신설 동기 사건
- `docs/operations/cd-auto-prune-2026-04-16.md` (OPS-016) — node-exporter / otel-collector / jaeger 도입 회고
- `docs/operations/alerting-audit-2026-04.md` — alertmanager wiring audit (본 §3 의 sibling)
- `docs/operations/alert-pipeline-runbook.md` — 5 레이어 wiring 검증
- `agent_docs/mailboxes/team2/processed/20260426-0028-infrastructure-setup-discipline.md` (위임 메일)
