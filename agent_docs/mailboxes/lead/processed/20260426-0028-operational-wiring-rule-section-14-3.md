---
from: lead
to: lead
subject: operational-wiring-rule-section-14-3
created: 2026-04-25T15:28:40Z
priority: Lead-Approval  # [P0] 긴급, [Ask] 응답 요청, [FYI] 참고, [Lead-Approval] 리드 승인
---

# operational-wiring-rule-section-14-3

## 요약

OPS-028 (postgres WAL archive 19일 silent miss) 을 7번째로 만든 silent miss 시리즈 (KST / scheduler stdout / loguru posarg / prometheus rule_files / compose change-detection / greenlet / 그리고 OPS-028) 의 본질 패턴을 구조적으로 차단하기 위해 **`agent_docs/development-policies.md §14` Wiring Rule 을 1회성 인프라 셋업까지 확장하는 §14.3 신설**. 본 메일은 리드 self-mailbox 로 정책 작성을 큐에 등록.

## 맥락

### 본질 분석

CLAUDE.md §5 의 silent miss 회귀 6사례 + 본 OPS-028 의 7번째 사례는 모두 동일 구조:

| # | 사건 | 정의 위치 | 적용 검증 결손 |
|---|---|---|---|
| 1 | KST 통일 (2026-04-15) | `today_kst_str()` | 테스트 fixture 가 UTC 사용 → silent miss |
| 2 | scheduler stdout buffer | `PYTHONUNBUFFERED` 미설정 | 49분 동안 logs 0 bytes |
| 3 | loguru %-format mismatch | `logger.info("...%d...")` | regex 검사기에 빈틈 |
| 4 | prometheus rule_files | 상대 경로 | config 이동 시 39 rule 전체 silent miss |
| 5 | compose change-detection | bind-mount 파일 변경 | `docker compose up -d` 가 recreate 안 함 |
| 6 | greenlet transitive dep | `requirements.txt` 미명시 | 광범위 except 가 `success=False` 로 삼킴 |
| **7** | **OPS-028 WAL archive 권한** | **셋업 시 root:root 생성** | **archive_command 실패 19일 silent** |

본질 = **"정의 ≠ 적용"** 패턴이 코드 PR 외에도 적용됨에도 §14 Wiring Rule 의 강제 영역이 코드 변경에만 한정.

### 현재 §14 Wiring Rule 의 한계

development-policies.md §14 는 다음을 강제:
- 알림 파이프라인 5 레이어 (상태 머신 / NotificationRouter / 재시도 루프 / 메트릭 / 메타알림 규칙)
- 각 레이어의 "정의 위치 ≠ 적용 위치" 분리 명시
- 통합 테스트 또는 런타임 로그로 주입·기동 확인

**한계**: 이 모든 강제는 "*PR 단위*" 에서 작동. PR 머지 시점의 wiring 검증 ✓. 그러나:
- VM 프로비저닝, named volume 신설, secret rotation, certificate renewal 등 *1회성 인프라 작업* 은 PR 이 아님
- 이런 작업의 "정의 ≠ 적용" 은 명시적 강제선이 없음
- → OPS-028 같은 silent miss 가 19일간 visible 안 됨

## 요청

`agent_docs/development-policies.md` 에 **§14.3 운영 Wiring Rule (Operational Wiring Rule)** 신설.

### 제안 본문 (초안 — 리드 검토 후 다듬어 commit)

```markdown
### §14.3 운영 Wiring Rule — 1회성 인프라 셋업의 "정의 ≠ 적용"

§14 의 Wiring Rule (정의했다 ≠ 적용했다) 은 PR 단위 코드 변경에만 적용된다.
그러나 다음 1회성 인프라 작업도 동일 패턴의 silent miss 위험을 보유한다:

- VM 프로비저닝 / OS 셋업
- Docker volume 신설 (named or bind)
- secret / API token / 인증서 신규 등록
- archive 경로, 백업 경로, log rotation 경로 신설
- prometheus job, alertmanager receiver 추가
- 외부 서비스 (KIS, FRED, ECOS, Anthropic) credential 등록

이런 작업은 "코드 PR 이 아니므로 §14 가 안 잡는다" 의 사각지대였다.
OPS-028 사건 (postgres WAL archive 19일 silent miss) 이 직접 사례.

본 §14.3 은 다음 3개 invariant 를 1회성 인프라 작업에 강제한다:

1. **Setup-time verification** — 작업 직후 *작동 검증* 을 명시 수행. 단순 "
   생성 완료" 가 아니라 "의도된 사용 경로가 end-to-end 흐름" 을 확인.
   - 예 (OPS-028 회피): wal_archive 볼륨 신설 직후 postgres 가 실제 cp 성공
     하는지 1회 trigger 후 확인 (e.g., 강제 CHECKPOINT + `pg_stat_archiver`
     쿼리)

2. **Continuous verification** — 시간이 지나도 invariant 가 유지되는지 *지속*
   확인하는 매트릭/스크립트/알림 중 최소 1개 채널 보유. set-and-forget 금지.
   - 매트릭 채널: prometheus exporter (예: postgres_exporter,
     mongodb-exporter)
   - 스크립트 채널: `scripts/check_*.py` 정기 실행 (cron, GHA scheduled,
     systemd timer)
   - 알림 채널: 매트릭/스크립트 결과가 임계 초과 시 alertmanager 트리거

3. **Self-documenting setup** — 셋업 절차가 `docs/operations/` 의 reusable
   checklist 또는 runbook 으로 기록. "마이그레이션 mailbox 만 보면 무엇을
   했는지 추적 가능" 한 audit trail.

### 강제 적용 시점

- 신규 인프라 작업 시: **시작 전** 본 §14.3 의 3 invariant 가 어떻게 충족되는지
  PR 본문 (인프라 설정이 IaC 라면) 또는 OPS 회고 문서 (수동 작업이라면) 에 명시.
- 기존 인프라 작업의 backfill: 팀 2 가 `docs/operations/infrastructure-setup-checklist.md`
  를 통해 모든 기존 invariant 를 audit (별도 위임 — 팀 2 메일 참조).

### 회귀 방어선

§14.1 / §14.2 와 마찬가지로 본 §14.3 은 **violation 사례를 §5 회귀 사례 로그에**
기록하여 학습 트레일을 남긴다. 본 §14.3 신설 자체가 OPS-028 의 후속 회귀
방어선이며, 향후 silent miss 시리즈 #8 이 본 정책 누락으로 발생하면 §14.3 의
적용 범위를 다시 확대해야 한다.
```

### 동시 갱신 — `CLAUDE.md §5`

§5 "최근 회귀 사례" 에 **OPS-028 entry 추가**:

```markdown
- **postgres WAL archive 권한 19일 silent miss (2026-04-26, OPS-028)**:
  `aqts_postgres_wal_archive` 볼륨이 인프라 셋업 시점부터 root:root drwxr-xr-x
  로 생성되어 postgres archive_command 가 19일간 매분 Permission denied 실패.
  pg_wal/ 가 13.5GB 까지 누적되며 디스크 98% → catch-up 폭주 → 100% PANIC →
  mongodb docker network corruption cascade. 35분 P0 incident, 데이터 손실 0.
  본질: §14 Wiring Rule 이 1회성 인프라 셋업에 적용되지 않은 사각지대 →
  §14.3 신설 (`development-policies.md`).
```

### 동시 갱신 — `CLAUDE.md §9` (TODO)

CLAUDE.md §9 미해결 TODO 에 OPS-028 후속 3축 추가:

```markdown
- [ ] **OPS-028 후속 — wal_archive 자동 chown (P0)**: `docker-compose.yml`
  postgres entrypoint override 또는 init container. 팀 2 위임 메일 …
- [ ] **OPS-028 후속 — pg_stat_archiver 알림 신설 (Ask)**: postgres_exporter
  + `aqts_postgres` group. 팀 2 위임 메일 …
- [ ] **OPS-028 후속 — boot disk 48GB → 100GB 증설 (Ask, deadline 2026-04-26
  23:59 KST)**: Mac 측 로컬 gcloud. 리드 self 메일 …
- [ ] **§14.3 운영 Wiring Rule 신설 (Lead-Approval)**: 본 메일 처리 시 활성화.
  팀 2 backfill audit + 팀 4 invariant scanners 자매 작업과 함께 진행.
```

## 게이트

- 본 정책 추가는 `agent_docs/development-policies.md` (리드 deny 영역) 편집 →
  *리드 본인이 직접 commit*. 다른 세션 / 팀에 위임 불가.
- 동시 PR 에 CLAUDE.md §5 + §9 갱신 동봉 (단일 atomic commit).
- 게이트: ruff/black 무영향 (.md 만), `check_doc_sync.py` 0 errors,
  `check_bool_literals.py` PASS. 문서-only 예외 적용 가능.

## 응답 기한

**리드 self-deadline = 2026-04-29 (W1 마감 전후)**. 본 정책이 후속 자매 작업
(팀 2 checklist, 팀 4 scanners) 의 SSOT 이므로 자매 작업 시작 전 머지 필요.
지연 시 자매 작업 → 정책 부재 상태 → 정책의 의도된 enforcement 결손.

## 자매 메일 (동시 위임)

- `agent_docs/mailboxes/team2/inbox/20260426-0028-infrastructure-setup-discipline.md`
  (팀 2 — checklist + 기존 인프라 backfill audit + log-based silent miss
  alert)
- `agent_docs/mailboxes/team4/inbox/20260426-0028-infrastructure-invariant-scanners.md`
  (팀 4 — `scripts/check_infra_invariants.py` 또는 분할된 invariant
  scanners + 회귀 테스트 하니스 + Doc Sync 워크플로 등록)

3개 메일은 의존성 chain:
**self-mailbox (Lead policy) → 팀 2 mailbox → 팀 4 mailbox**.

리드 정책 머지 후 팀 2 가 checklist + alert 실행, 팀 4 가 scanner 자동화.

## 참조

- `docs/operations/postgres-wal-archive-permission-2026-04-26.md` (OPS-028 회고 SSOT)
- `agent_docs/development-policies.md §14` (현 Wiring Rule 본문)
- `CLAUDE.md §5` (silent miss 회귀 사례 누적)
- `agent_docs/development-policies.md §8` (Silence Error 의심 원칙 — 본 §14.3 의
  근간 철학)
