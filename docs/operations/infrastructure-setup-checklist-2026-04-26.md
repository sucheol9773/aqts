# 인프라 셋업 체크리스트 — 운영 Wiring Rule §14.5 집행 — 2026-04-26

> **문서 번호**: OPS-029
>
> **목적**: 모든 1회성 인프라 작업 (VM 프로비저닝 / 볼륨 신설 / secret 등록 / prometheus job / 알림 receiver / DNS·인증서 등) 이 `agent_docs/development-policies.md §14.5` 의 3 invariant (setup-time / continuous / self-documenting) 를 만족하도록 강제하는 운영 implementation 체크리스트. OPS-028 (postgres WAL archive 19일 silent miss) 의 "본질 = §14 가 1회성 인프라 셋업에 적용 안 됨" 회고에 대한 §14.5 정책 (PR #58) 의 운영 implementation. 팀 2 위임 메일 `agent_docs/mailboxes/team2/inbox/20260426-0028-infrastructure-setup-discipline.md` 축 1 산출물.

---

## 1. 적용 범위

본 체크리스트는 다음 1회성 인프라 작업에 적용한다:

- **VM / 호스트**: GCP Compute Engine 인스턴스 프로비저닝, OS 셋업, boot disk / data disk 추가
- **Docker 볼륨**: named volume / bind mount 신설, 기존 볼륨 mount 옵션 변경
- **Secret / credential**: 신규 외부 서비스 (KIS, FRED, ECOS, Anthropic, Telegram bot 등) 인증, JWT signing key, MFA secret, GitHub PAT
- **저장소 경로**: archive 경로 (postgres WAL archive, mongodb backup), log rotation
- **모니터링**: prometheus job 추가, alertmanager receiver 신규
- **네트워크**: DNS 레코드, TLS 인증서, 방화벽 규칙 (firewall-rules), VPC peering
- **CI/CD secret**: GitHub Actions repository secret, OIDC provider 설정

**적용 제외**:
- 코드 PR (이미 §14.1~14.4 가 강제)
- 일시적인 임시 작업 (ad-hoc debug, hotfix 직후 즉시 롤백)
- 위 카테고리에 속하지 않는 운영 작업 (예: PR review, 백테스트 실행)

---

## 2. 작업별 invariant 체크

각 카테고리의 모든 [ ] 항목을 작업 시작 전 검토하고, 각 항목의 검증 결과를 작업 PR 본문 또는 OPS 회고 문서에 inline quote 한다. 미충족 항목이 있으면 작업 보류 — 충족 가능한 후속 작업 위임 후 진행.

### A. Docker named volume / bind mount 신설 시

OPS-028 의 직접 학습 카테고리. wal_archive 볼륨이 root:root 로 생성되어 19일간 archive 실패가 silent miss 였다.

- [ ] **owner / mode 일치**: 볼륨 owner (`uid:gid`) 와 mode (`drwx------` 등) 가 사용 컨테이너의 user/group 과 일치. 검증:
  ```bash
  docker exec <container> stat <mount-point> | grep -E 'Uid|Access'
  ```
- [ ] **컨테이너의 자동 chown**: 볼륨 첫 마운트 시 root:root 로 생성되는 docker named volume 의 패턴을 이해. 사용 컨테이너의 entrypoint 또는 init script 가 자동으로 chown 하는지 확인. 미수행 시 entrypoint override 또는 init container 패턴 (OPS-028 fix 참조).
- [ ] **실제 write 검증 (1회 trigger)**: 단순 마운트 성공이 아닌, 사용 컨테이너가 *실제로 데이터를 쓰는* 1회 trigger 까지 끝까지 통과하는지 확인. 예: postgres archive_command, mongodb backup script, redis dump.
- [ ] **점유 모니터링 매트릭**: 볼륨 점유율 / 크기를 prometheus 매트릭으로 노출. node_exporter `node_filesystem_*` 또는 service-specific exporter (예: postgres_exporter `pg_wal_size_bytes`).
- [ ] **임계 알림 1개 이상**: 매트릭 기반 alertmanager 알림 1개 이상 신설. `for: <duration>` 과 severity 명시.
- [ ] **PR 본문 / OPS 회고 inline 검증**: 위 5 항목의 검증 결과를 IaC PR 본문 (compose 변경) 또는 OPS 회고 문서 (수동 작업) 에 inline quote — `docker exec ... stat` 출력, `curl /metrics | grep` 출력, alert rule yaml 발췌.

### B. VM / 호스트 프로비저닝 시

OPS-028 의 §4 monitoring 격차 + boot disk 48GB 제한 학습.

- [ ] **boot disk size ≥ 100GB**: OPS-028 학습 (48GB 환경에서 13.5GB pg_wal 누적이 곧장 디스크 압박). 단일 VM 환경의 운영 안전 마진.
- [ ] **service account scope**: 필요 IAM scope (compute admin, storage admin, log writer 등) 가 VM 의 service account 에 부여되어 있는지. OPS-028 boot disk resize 시 in-VM gcloud 가 compute scope 부재로 실패한 사례 회피.
- [ ] **node-exporter 등 host-level 매트릭 expose**: `node_filesystem_*`, `node_cpu_seconds_total`, `node_memory_*` 등이 prometheus targets 에 up=1.
- [ ] **SSH access 양방향**: lead Mac (workstation) + GCP console UI 양쪽 경로 모두 작동 확인. compute scope 부재 등으로 한쪽 차단 시 incident response 시 우회 경로 부재.
- [ ] **AqtsDiskUsageHigh / AqtsDiskUsageCritical 트리거 검증**: 의도적 fill 테스트 (staging only) 또는 historical baseline (이미 발생한 alert 의 fire 이력) 으로 알림 자체가 작동하는지 1회 검증.
- [ ] **OS 패키지 보안 baseline**: `apt-get update && apt-get upgrade -y` 또는 `unattended-upgrades` 설정. grype CVE scan 의 OS 패키지 baseline 일치.

### C. Prometheus job / alertmanager receiver 추가 시

§14.1~14.5 의 "정의 ≠ 적용" 패턴이 가장 직접 적용되는 카테고리.

- [ ] **target up=1**: 신규 scrape target 이 Prometheus UI `/targets` 에서 `up=1`. 컨테이너 health 만으로는 부족 (DNS / 포트 / TLS 검증 필수).
- [ ] **매트릭 실제 expose**: 의도된 매트릭이 실제로 노출되는지 직접 확인:
  ```bash
  curl -s http://<exporter>:<port>/metrics | grep <expected_metric>
  ```
- [ ] **alert rule promtool 검증**: 신규 알림 규칙은 `promtool check rules` 0 errors:
  ```bash
  docker run --rm -v $(pwd)/monitoring/prometheus/rules:/rules \
    prom/prometheus:v2.53.0 promtool check rules /rules/aqts_alerts.yml
  ```
- [ ] **alertmanager receiver 매핑**: 알림 routing 이 실제 receiver 로 전달되는지:
  ```bash
  docker run --rm -v <alertmanager.yml>:/cfg.yml \
    prom/alertmanager:v0.27.0 amtool check-config /cfg.yml
  ```
- [ ] **합성 테스트로 알림 트리거**: staging 또는 dev 환경에서 의도적으로 trigger 조건 충족 → 5분 내 alert active 확인:
  ```bash
  curl -s http://localhost:9093/api/v2/alerts \
    | jq -r '.[] | select(.labels.alertname=="<NewAlert>") | .status.state'
  # 기대: "active"
  ```
  **프로덕션 적용 금지** — staging only.
- [ ] **관측 공백 방어 (paired alert)**: 신규 매트릭 source 의 `absent(up==1)` 패턴 알림 1개 추가. node-exporter / postgres-exporter 의 `*ExporterMissing` 패턴 (OPS-028 §4.1).

### D. Secret / credential 신규 등록 시

- [ ] **`.env.example` 키 이름 추가**: 값 절대 금지. 키 이름 + 한 줄 설명만:
  ```bash
  # External APIs
  KIS_APP_KEY=<발급-방법-링크>
  ```
- [ ] **공급망 보안 정책 준수**: `agent_docs/development-policies.md §13` (cosign / grype / pip-audit) 에 영향 검토. 새 의존성이 추가되면 SBOM 갱신.
- [ ] **만료일 monitoring**: credential 이 만료일을 가지면 (예: GitHub PAT, TLS 인증서, JWT signing key rotation) 만료일 모니터링 채널 1개 이상. 수동 calendar reminder 또는 `cert-manager` / 자동 매트릭.
- [ ] **rotation 절차 문서화**: `docs/operations/<service>-credential-rotation.md` 또는 기존 OPS 문서 내 한 절. revoke + re-issue + redeploy + verification 4 단계 명시.
- [ ] **회귀 검증**: rotation 절차를 실제로 실행해보았는가? 미실행이면 첫 만료 시점이 incident 가 됨 (PAT 만료 silent miss 패턴).

### E. 외부 서비스 신규 의존 (KIS, FRED, ECOS, Anthropic, Telegram bot, Slack webhook, GitHub MCP 등)

- [ ] **D 의 모든 항목**: credential 등록 카테고리 적용.
- [ ] **rate limit 인지**: 외부 API 의 rate limit / quota 를 측정하고 본 시스템의 사용 추정치가 그 이하인지 확인. 초과 시 circuit breaker / 캐시 / batch 전략.
- [ ] **fallback 경로**: 외부 서비스 다운 시 graceful degradation. circuit breaker (`backend/core/circuit_breaker/` 패턴) 또는 stub 응답.
- [ ] **응답 schema validation**: 외부 응답 schema 가 변경되어도 silent miss 없이 fail fast 하는지. pydantic strict mode 또는 contract test.
- [ ] **monitoring**: 외부 호출 성공률, 레이턴시, error rate 매트릭. `aqts_external_api_*` 패턴.

### F. CI/CD secret / OIDC / repository setting

- [ ] **GitHub repository secret 등록**: `gh secret set` 명령으로 등록. UI 입력 시 작업 기록 남기지 않음 — UI 입력해도 별도 audit 로그에 적힘.
- [ ] **secret rotation 가능성**: `.github/workflows/*.yml` 에서 secret 사용 위치 grep 후 rotation 시 영향 범위 파악.
- [ ] **OIDC provider 설정**: cosign keyless / GCP workload identity 등은 OIDC 신뢰 관계가 핵심. `aud` claim, `sub` claim 의 정확한 값 검증.
- [ ] **branch protection / required checks**: 신규 CI 게이트 추가 시 branch protection 의 required checks 에 등록. 등록 누락 시 PR 가 fail 인 채로도 머지되는 silent miss.

---

## 3. 본 체크리스트 사용법

1. 인프라 작업 시작 **전** 본 문서의 §해당 섹션 항목을 모두 검토.
2. 각 [ ] 의 검증 결과를 작업 PR 본문 (IaC) 또는 OPS 회고 문서 (수동) 에 quote.
3. 미충족 항목이 있으면 **작업 보류** — 충족 가능한 후속 작업을 다른 팀에 위임하거나, 본인이 별도 작업으로 분리.
4. 작업 완료 후 §14.5 의 self-documenting invariant 충족을 위해 OPS 회고 문서에 본 체크리스트 링크 명시.

---

## 4. 후속 카테고리 추가

본 체크리스트의 §2 카테고리 (A~F) 는 OPS-028 + 1차 리뷰 시점의 enumerate 결과. 추가 카테고리가 발견되면 본 문서를 갱신하지 말고 **새 OPS 문서로 분리** 하여 본 문서가 계속 슬림하게 유지되도록 한다.

확정 안 된 후속 카테고리 후보:
- **Kubernetes / Helm 도입 시** — 현재 단일 VM compose 환경이라 미작성. K8s 도입 결정 시 신규 OPS.
- **multi-region failover 도입 시** — 단일 region 운영 종료 시 신규 OPS.
- **외부 PII 데이터 흐름 도입 시** — 현재 PII 0 정책이라 미작성. PII 도입 결정 시 신규 OPS.

---

## 5. 회귀 방어선

본 문서의 self-application:

- 본 문서 PR 자체가 §2.E (외부 서비스 의존) 에 해당하지 않으나, §2.D (credential) 는 N/A, §2.A (볼륨) 는 N/A — 모든 카테고리가 N/A.
- 향후 본 문서를 인용하는 인프라 작업 PR 은 본 문서의 §2 해당 카테고리 모든 [ ] 항목 검증 결과를 PR 본문에 quote 해야 함.
- 본 문서 자체의 evolution: §2 카테고리 갱신은 별도 OPS 문서로 분리 (§4 참조).

---

## 6. 자매 작업

본 체크리스트는 OPS-028 §14.5 후속 3축 작업의 **축 1**:

- **축 1 (본 문서)**: `infrastructure-setup-checklist-2026-04-26.md` (OPS-029) — 체크리스트 docs
- **축 2** (예정 2026-05-13 deadline): 12 docker volume + 11 prometheus job + alertmanager receiver backfill audit. 결과 → `infrastructure-backfill-audit-2026-04-NN.md` (별도 OPS 신규)
- **축 3** (예정 2026-05-20 deadline, B/C 선택 후): log-based silent miss alert. 옵션 B (postgres_exporter — PR #60 으로 부분 해소) + 옵션 C (stdout pattern scanner — 팀 4 invariant scanner 와 통합)

자매 메일:
- `agent_docs/mailboxes/team2/inbox/20260426-0028-infrastructure-setup-discipline.md` (본 작업 위임)
- `agent_docs/mailboxes/team4/inbox/20260426-0028-infrastructure-invariant-scanners.md` (팀 4 — 자동 검증 스크립트 paired)
- `agent_docs/mailboxes/team2/inbox/20260426-0014-wal-archive-auto-chown.md` (PR #59, 표면 fix)
- `agent_docs/mailboxes/team2/inbox/20260426-0014-pg-stat-archiver-alert.md` (PR #60, monitoring 보강)

---

## 7. 관련 문서

- `agent_docs/development-policies.md §14.5` — 운영 Wiring Rule (본 문서의 SSOT 정책)
- `docs/operations/postgres-wal-archive-permission-2026-04-26.md` (OPS-028) — 본 정책 신설 동기 사건
- `agent_docs/development-policies.md §13` — 공급망 보안 정책 (D 카테고리 의존)
- `agent_docs/development-policies.md §14.1~14.4` — 코드 PR 의 wiring rule
- `CLAUDE.md §5` — silent miss 회귀 사례 누적 (본 정책 violation 사례 향후 추가)
