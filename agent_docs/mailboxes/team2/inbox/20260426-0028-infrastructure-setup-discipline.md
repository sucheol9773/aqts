---
from: lead
to: 2
subject: infrastructure-setup-discipline
created: 2026-04-25T15:28:40Z
priority: Ask  # [P0] 긴급, [Ask] 응답 요청, [FYI] 참고, [Lead-Approval] 리드 승인
---

# infrastructure-setup-discipline

## 요약

OPS-028 (postgres WAL archive 19일 silent miss) 의 본질 원인 = §14 Wiring Rule 이 1회성 인프라 셋업에 적용 안 됨. 리드가 §14.3 운영 Wiring Rule 정책을 신설할 예정 (자매 메일 `lead/inbox/20260426-0028-operational-wiring-rule-section-14-3.md`). 본 메일은 그 정책의 **운영 implementation** 을 팀 2 (인프라/모니터링 영역) 에 위임.

## 맥락

### 본질 패턴

CLAUDE.md §5 의 6 silent miss + OPS-028 (#7) 모두 "정의 ≠ 적용" 패턴. 그 중 §14 가 코드 PR 만 강제 → 1회성 인프라 작업이 사각지대. 본 메일은 그 사각지대를 **운영 절차 문서 + 기존 인프라 backfill audit + log-based alert** 3축으로 닫는 작업.

### 왜 팀 2 인가

governance.md §2.2 — 팀 2 영역:
- `docker-compose*.yml`
- `.github/workflows/*.yml` (CI/CD)
- `monitoring/prometheus/`, `monitoring/alertmanager/`
- `docs/operations/` (운영 런북)

본 작업의 산출물 (체크리스트 docs + alert rules + compose 검증) 모두 팀 2 영역.

## 요청 — 3 축 작업

### 축 1: `docs/operations/infrastructure-setup-checklist.md` 신설

**목적**: 모든 1회성 인프라 작업이 §14.3 의 3 invariant (setup-time / continuous / self-documenting) 를 만족하도록 강제하는 체크리스트.

**구조 (제안)**:

```markdown
# 인프라 셋업 체크리스트 (운영 Wiring Rule §14.3 집행)

## 적용 범위

- VM 프로비저닝 / OS 셋업
- Docker named volume / bind mount 신설
- secret / credential 신규 등록
- archive 경로, 백업 경로, log rotation 신설
- prometheus job, alertmanager receiver 추가
- 외부 서비스 (KIS, FRED, ECOS, Anthropic) credential
- DNS, 인증서, 방화벽 규칙

## 작업별 invariant 체크

### A. Docker named volume 신설 시
- [ ] 볼륨 owner / mode 가 **사용 컨테이너의 user/group 과 일치**
- [ ] 볼륨 사용 컨테이너가 **실제 write 성공** 검증 (1회 trigger)
- [ ] 볼륨 점유 모니터링 매트릭 노출 (예: postgres_exporter 의 pg_wal_size,
      mongodb-exporter 의 storage size)
- [ ] alertmanager 알림 규칙 1개 이상 (점유 임계 또는 invariant 실패)
- [ ] OPS 회고 또는 IaC PR 본문에 위 4 항목 명시

### B. VM 프로비저닝 시
- [ ] boot disk size ≥ 100GB (OPS-028 학습)
- [ ] service account 에 필요 scope (compute admin, storage admin) 부여
- [ ] node-exporter 등 host-level 매트릭 expose 확인
- [ ] SSH access 가 lead Mac + GCP console 양쪽 경로 작동
- [ ] AqtsDiskUsageHigh / AqtsDiskUsageCritical 알림 트리거 검증 (의도적 fill
      테스트 또는 historical baseline)

### C. Prometheus job / alertmanager receiver 추가 시
- [ ] target 이 `up=1` (Prometheus UI `/targets`)
- [ ] 매트릭이 실제 expose (`curl /metrics | grep <expected>`)
- [ ] alert rule promtool 검증
- [ ] alertmanager 가 receiver 매핑 (`amtool check-config`)
- [ ] 합성 테스트로 알림 트리거 검증 (e.g., 의도적 fail)

### D. Secret / credential 신규 등록 시
- [ ] `.env.example` 에 키 이름 추가 (값 절대 금지)
- [ ] `agent_docs/development-policies.md §13` 공급망 보안 정책 준수
- [ ] credential 만료일이 monitoring 대상 (수동 calendar 또는 자동 매트릭)
- [ ] credential rotation 절차가 `docs/operations/` 의 어딘가에 문서화

(다른 카테고리는 backfill 진행하면서 추가)

## 본 체크리스트 사용법

1. 인프라 작업 시작 전 본 문서 §해당 섹션 의 [ ] 항목 모두 검토
2. 각 항목의 검증 결과를 작업 PR 본문 또는 OPS 회고 문서에 quote
3. 미충족 항목이 있으면 **작업 보류** — 충족 가능한 후속 작업 위임 후 진행
```

### 축 2: 기존 인프라 backfill audit (one-time)

**목적**: 본 체크리스트 신설 *이전* 에 만들어진 모든 인프라가 §14.3 invariant 를 충족하는지 1회 audit.

**작업**:
1. 모든 docker volume 12 개 audit (owner / mode / 사용 컨테이너 user / write 검증):
   - `aqts_alertmanager_data`, `aqts_backup_data`, `aqts_grafana_data`,
     `aqts_mongodb_data`, `aqts_postgres_data`, `aqts_postgres_wal_archive`,
     `aqts_prometheus_data`, `aqts_redis_data` (named)
   - `436d809b…`, `a5e8e0f0…`, `a9b59358…`, `c90fbe28…` (anonymous, docker
     prune 대상 검토 — 사용 중 컨테이너 확인 후 정리)
2. 모든 prometheus job audit (target up / metric expose / alert rule mapping):
   - `aqts-backend`, `aqts-scheduler`, `aqts-postgres`, `aqts-mongodb`,
     `aqts-redis`, `aqts-node-exporter`, `aqts-jaeger`, `aqts-otel-collector`,
     `aqts-prometheus`, `aqts-alertmanager`, `aqts-grafana`
3. 모든 alertmanager receiver audit (`amtool check-config` + 합성 테스트)
4. 결과를 `docs/operations/infrastructure-backfill-audit-2026-04-NN.md` 로
   기록. 발견된 미충족 항목은 별도 fix PR 시리즈로 위임.

### 축 3: log-based silent miss alert (Loki 또는 stdout-pattern)

**목적**: postgres archive_command 같은 stderr/stdout 만 찍히고 prometheus 매트릭 없는 silent miss 를 alert 채널로 끌어올림.

**선택지** (팀 2 판단):

A. **Loki + Promtail 도입** — docker logs 를 Loki 에 수집, alertmanager 가
   Loki query 기반 알림. 새 컨테이너 2 개 (Loki, Promtail) + storage 추가.
   장기적으로 가장 강력하지만 운영 부담 증가.

B. **postgres_exporter + 다른 exporter** 로 매트릭 채널 확보 — 자매 메일
   `pg-stat-archiver-alert.md` 와 통합. log 가 아닌 매트릭 source 확보가
   더 안정적. **리드 권장**.

C. **stdout pattern scanner script** — stopgap. 팀 4 의 invariant scanner
   안에서 docker logs 의 패턴 검색 (예: `archive command failed`,
   `Permission denied`, `OOM`, `panic`). cron 또는 systemd timer 로 정기
   실행. exporter 도입 전까지의 임시 채널.

리드 권장 = **B + C 병행**. C 는 즉시 가능 (팀 4 작업의 일부), B 는 자매 메일에서 다룸.

## 의존성

- **선결**: 리드의 §14.3 정책 머지 (자매 메일 처리 후, deadline 2026-04-29)
- **병행**: 팀 4 의 `scripts/check_infra_invariants.py` (자매 메일
  `team4/inbox/20260426-0028-infrastructure-invariant-scanners.md`) — 본 메일의
  축 2 audit 결과를 입력으로 받아 자동화

## 게이트

- ruff/black 무영향 (.md + .yml 변경)
- `check_doc_sync.py` 0 errors / 0 warnings
- 신규 prometheus rule 시 `promtool check rules` 0 errors
- 본 체크리스트가 자기 적용성 (self-application) 통과 — 본 PR 자체가
  체크리스트 §A 의 모든 항목을 만족하는지 PR 본문에 inline quote

## 응답 기한

**합의 응답**: 2026-04-29 (W1 마감 전후) — 축 1 ~ 3 의 작업 분담 + 축 3 의 A/B/C 선택 회신.

**구현 머지**:
- 축 1 (체크리스트 docs) 만 우선 = **2026-05-06 (Stage 2 Exit) 이전**
- 축 2 (backfill audit) = **2026-05-13** — 결과에 따라 fix PR 시리즈 후속
- 축 3 (log-based alert) = **B/C 선택 후 2026-05-20** — Loki 채택 시 추가 시간 필요

## 자매 메일

- `agent_docs/mailboxes/lead/inbox/20260426-0028-operational-wiring-rule-section-14-3.md`
  (리드 self — §14.3 정책 신설, 본 메일의 SSOT)
- `agent_docs/mailboxes/team4/inbox/20260426-0028-infrastructure-invariant-scanners.md`
  (팀 4 — invariant 자동 검증 스크립트, 본 메일 축 2 audit 의 자동화 paired)
- `agent_docs/mailboxes/team2/inbox/20260426-0014-wal-archive-auto-chown.md`
  (팀 2 — OPS-028 표면 fix, 본 메일은 본질 fix)
- `agent_docs/mailboxes/team2/inbox/20260426-0014-pg-stat-archiver-alert.md`
  (팀 2 — OPS-028 monitoring 보강, 본 메일 축 3 의 옵션 B 와 동일 source)

## 참조

- `docs/operations/postgres-wal-archive-permission-2026-04-26.md` (OPS-028 회고)
- `agent_docs/development-policies.md §14` (Wiring Rule 본문 — §14.3 추가
  대상)
- `CLAUDE.md §5` (silent miss 회귀 사례 누적)
- `agent_docs/governance.md §2.2` (팀 2 영역 정의)
