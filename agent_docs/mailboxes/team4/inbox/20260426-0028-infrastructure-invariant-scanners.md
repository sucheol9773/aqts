---
from: lead
to: 4
subject: infrastructure-invariant-scanners
created: 2026-04-25T15:28:40Z
priority: Ask  # [P0] 긴급, [Ask] 응답 요청, [FYI] 참고, [Lead-Approval] 리드 승인
---

# infrastructure-invariant-scanners

## 요약

OPS-028 본질 원인 (§14 Wiring Rule 의 1회성 인프라 사각지대) 의 **자동 검증 레이어** 를 팀 4 (정적 검사기 + 회귀 테스트) 영역으로 위임. 리드가 §14.3 정책을 신설하고 (자매 메일), 팀 2 가 체크리스트 + alert 운영 측면을 담당하고 (자매 메일), 팀 4 는 invariant 가 시간이 지나도 유지되는지 *지속적으로 자동 검증* 하는 스캐너 시리즈를 신설.

## 맥락

### 본질 패턴 — 본 메일의 차별점

- 리드 §14.3 = **정책** (무엇을 verify 해야 하는가)
- 팀 2 backfill audit = **현재 시점의 1회 audit + 운영 SOP**
- 팀 4 = **invariant 가 시간 경과 후에도 유지되는지의 지속적 확인** (set-and-forget 방지)

OPS-028 사건의 핵심은 "셋업 시점에 권한이 root:root 였다" 가 아니라 "**셋업 후 19일간 아무도 다시 확인 안 했다**". 즉 setup-time verification 만으로는 부족 — *continuous verification* 이 본 메일의 분담분.

### 왜 팀 4 인가

governance.md §2.4 — 팀 4 영역:
- `backend/scripts/check_*.py`
- `scripts/check_*.py` (root scripts/)
- `backend/tests/test_check_*.py` 회귀 테스트 하니스
- Doc Sync 워크플로 등록

기존 OPS-019/020/022/026 의 정적 검사기 시리즈와 동일 패턴 재사용 가능.

## 요청 — Invariant Scanner 시리즈

### 1. 핵심 스캐너 — `scripts/check_infra_invariants.py`

**목적**: 단일 스크립트로 모든 1회성 인프라 invariant 를 검증. cron / GHA scheduled / systemd timer 로 정기 실행.

**검사 카테고리** (각각 별도 함수, 결과는 0 errors 강제 또는 warning):

#### 1.1 Docker volume 권한 invariant
```python
def check_volume_permissions(volume_name: str, expected_user: str, expected_perm: int) -> tuple[bool, str]:
    """`docker run --rm -v VOL:/mnt alpine stat -c '%U %a' /mnt` 로 owner/perm 확인.
    OPS-028 의 root:root 회귀 직접 차단.
    """
```

검증 대상:
- `aqts_postgres_wal_archive` → `postgres / 700`
- `aqts_postgres_data` → `postgres / 700` (PGDATA)
- `aqts_mongodb_data` → `mongodb / 700`
- `aqts_redis_data` → `redis / 755`
- `aqts_grafana_data` → `grafana / 755`
- `aqts_prometheus_data` → `nobody / 755` (prometheus 가 nobody UID 65534)
- (다른 볼륨은 팀 2 backfill audit 에서 상세 확인)

#### 1.2 Docker network IP invariant
```python
def check_container_ip(container_name: str) -> tuple[bool, str]:
    """`docker inspect <c> --format '{{range $k, $v := .NetworkSettings.Networks}}{{$v.IPAddress}}{{end}}'`
    이 'invalid IP' 가 아닌 정상 IPv4 인지 확인.
    OPS-028 의 mongodb network corruption 회귀 직접 차단.
    """
```

검증 대상: 모든 service container 12 개.

#### 1.3 Healthcheck reach invariant (외부 도달성)
```python
def check_external_reach(container_name: str, target_host: str, target_port: int) -> tuple[bool, str]:
    """다른 컨테이너에서 `getent hosts <target>` + `nc -zv <target> <port>` 가
    실제로 응답하는지. healthcheck 가 컨테이너 내부 self-ping 만 보는 한계 보완.
    """
```

검증 대상:
- backend → postgres / mongodb / redis 도달성
- scheduler → postgres / mongodb / redis 도달성
- prometheus → 모든 scrape target 도달성

#### 1.4 PostgreSQL archive 진행률 invariant
```python
def check_pg_archiver(pg_container: str) -> tuple[bool, str]:
    """`SELECT failed_count, last_archived_time FROM pg_stat_archiver` 결과를
    검증. failed_count 가 0 이거나, last_archived_time 이 5분 이내. OPS-028 의
    19일 silent miss 회귀 직접 차단.
    """
```

#### 1.5 docker logs silent error 패턴 스캐너 (stopgap)
```python
def check_recent_log_patterns(container_name: str, patterns: list[str], since: str = "1h") -> tuple[bool, str]:
    """`docker logs <c> --since <since>` 에서 silent miss 후보 패턴이 있는지
    검색. 자매 메일 (팀 2 축 3 옵션 C) 의 "stdout pattern scanner" 분담.
    """
```

검출 패턴:
- `archive command failed`
- `Permission denied`
- `panic` / `PANIC`
- `OOM`
- `No space left on device`
- `name resolution` 실패
- `Connection refused` 지속 반복

### 2. CLI 인터페이스

```bash
# 전체 invariant 검사 (CI / cron 사용)
python scripts/check_infra_invariants.py --all

# 단일 카테고리만
python scripts/check_infra_invariants.py --category volume-permissions
python scripts/check_infra_invariants.py --category network-ip
python scripts/check_infra_invariants.py --category external-reach
python scripts/check_infra_invariants.py --category pg-archiver
python scripts/check_infra_invariants.py --category log-patterns

# JSON 출력 (alertmanager 또는 다른 도구 통합)
python scripts/check_infra_invariants.py --all --output-json
```

exit code:
- 0: 모든 invariant 충족
- 1: invariant 위반 (해당 항목 stderr 에 reason)
- 2: 사용법 오류 / 도구 부재 (docker 미설치 등)

### 3. 회귀 테스트 하니스 (6 그룹 패턴 — OPS-022/026/022 재사용)

`backend/tests/test_check_infra_invariants.py`:

1. **유효 통과** — 정상 invariant 상태 (mock or 실제 환경) PASS
2. **위반 검출** — 각 카테고리당 1 케이스 위반 시 deny (총 ≥ 5 tests)
3. **오탐 방지** — 정상 패턴이 false positive 없음 (예: docker logs 의 "archive command" 가 정상 메시지 일부일 때 silent miss 와 구분)
4. **파서 견고성** — `docker inspect` 출력 변형, JSON 키 누락, 빈 결과 등 핸들링
5. **실제 환경 회귀** — 본 작업 시점의 main 환경에서 모든 카테고리 PASS 확인 (단, OPS-028 의 wal_archive 자동 chown 머지 전에는 일부 expected violation)
6. **`main()` 진입** — argparse / exit code / 카테고리 선택 사용법 출력

목표 ≥ 18 tests.

### 4. 정기 실행 채널 — 3 옵션

A. **GitHub Actions scheduled workflow** — `.github/workflows/infra-invariants-cron.yml`. 매일 1회. 실패 시 GHA failure 가 알림 (slack/telegram webhook).
B. **systemd timer on aqts-server** — 매시간. 결과를 `/var/log/aqts-invariants.log` 에 append + 위반 시 alertmanager 직접 push.
C. **prometheus job + textfile collector** — node-exporter 의 textfile collector 로 매트릭 expose. alertmanager 가 매트릭 기반 알림.

리드 권장 = **A + C 병행**. A 는 GHA infra 가 이미 갖춰져 있어 즉시 가능. C 는 prometheus 매트릭으로 기존 alertmanager pipeline 통합.

### 5. Doc Sync 워크플로 등록

`.github/workflows/doc-sync-check.yml` (또는 동등 워크플로) 의 vuln-ignore parity / expiry 스텝 인근에 다음 추가:

```yaml
- name: Run infra invariants check
  run: python scripts/check_infra_invariants.py --all --category log-patterns
  # 다른 카테고리는 docker daemon 필요 → CI 가 docker-in-docker 미지원이면 skip,
  # log-patterns 만 PR 단계에서 강제 (정적 분석 가능 영역).
```

## 의존성

- **선결**: 리드 §14.3 정책 머지 (자매 메일, deadline 2026-04-29)
- **병행**: 팀 2 backfill audit (자매 메일) — 결과 = 본 스캐너의 *expected baseline* 으로 사용

## 게이트

- ruff / black PASS
- `pytest backend/tests/test_check_infra_invariants.py` ≥ 18 tests PASS
- `python scripts/check_infra_invariants.py --all` 가 본 PR 머지 후 main 환경에서 0 errors
- self-test: 의도적 invariant 위반 (예: 임시 wal_archive chown 변경) 후 스캐너가 즉시 detect → 복구

## 응답 기한

**합의 응답**: 2026-04-29 (W1 마감 전후) — 카테고리 분담 + 정기 실행 채널 (A/B/C) 선택 + 테스트 하니스 구조 회신.

**구현 머지**:
- 핵심 스캐너 + 회귀 테스트 = **2026-05-13 (Stage 2 Exit + 1주 마진)**
- Doc Sync 워크플로 등록 + 정기 실행 채널 = **2026-05-20**

## 자매 메일

- `agent_docs/mailboxes/lead/inbox/20260426-0028-operational-wiring-rule-section-14-3.md`
  (리드 self — §14.3 정책)
- `agent_docs/mailboxes/team2/inbox/20260426-0028-infrastructure-setup-discipline.md`
  (팀 2 — checklist + backfill audit + log-based alert; 본 메일의 paired
  운영 측면)

## 참조

- `docs/operations/postgres-wal-archive-permission-2026-04-26.md` (OPS-028)
- `agent_docs/development-policies.md §14` (Wiring Rule)
- `scripts/check_vuln_ignore_parity.py` (OPS-022 패턴 원형)
- `scripts/check_vuln_ignore_expiry.py` (OPS-026 패턴 원형, `check_expiry()` 시그니처 참고)
- `scripts/check_ownership_boundary.py` (PR-level checker 패턴)
- `agent_docs/governance.md §2.4` (팀 4 영역)
