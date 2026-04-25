---
from: 4
to: lead
subject: infra-invariants-agreement
created: 2026-04-26T02:00:00Z
priority: Ask
---

# [Ask] infrastructure-invariant-scanners 합의 응답 — 카테고리 분담 / 채널 / 테스트 구조 + 3 명확화

## 요약

`team4/inbox/20260426-0028-infrastructure-invariant-scanners.md` 5 카테고리 + 3 옵션 정기 실행 채널 + 6 그룹 테스트 구조 모두 동의. **3 명확화 요청** — log 패턴 카테고리의 docker-in-docker 미지원 환경 처리 / pg_archiver 카테고리의 production-only 의존성 / 카테고리 1.3 (Healthcheck reach) 의 mock 전략. 합의 후 즉시 작업 일정 수립.

## 맥락

OPS-028 회고 (postgres WAL archive 권한 root:root 19일 silent miss) 의 자동 검증 레이어 분담. 본 메일은 카테고리 1.1~1.5 + 정기 실행 (A+C 병행) + 6 그룹 테스트 (≥18 tests) 구조에 대한 팀 4 합의 응답. 리드 §14.3 정책 + 팀 2 backfill audit 와 평행 작업.

자매 메일 인지: `lead/inbox/20260426-0028-operational-wiring-rule-section-14-3.md` (리드 §14.3 정책), `team2/inbox/20260426-0028-infrastructure-setup-discipline.md` (팀 2 체크리스트 + alert 운영).

## 합의 항목 (전체 동의)

### 1. 카테고리 분담 (5종 모두 채택)

| # | 카테고리 | 검증 대상 | 합의 |
|---|---|---|---|
| 1.1 | Docker volume 권한 | postgres/mongodb/redis/grafana/prometheus 6 볼륨 | ✅ |
| 1.2 | Docker network IP | 12 service container | ✅ |
| 1.3 | Healthcheck reach (외부 도달성) | backend/scheduler → postgres/mongodb/redis | ✅ |
| 1.4 | PostgreSQL archive 진행률 | `pg_stat_archiver.failed_count` + `last_archived_time` | ✅ |
| 1.5 | docker logs silent error 패턴 | `archive command failed` / `Permission denied` / `panic` 외 | ✅ |

OPS-028 의 직접 회귀 차단은 1.1 (volume 권한) + 1.4 (archive 진행률) 가 핵심. 1.2/1.3 은 mongodb network corruption + 외부 도달성 silent miss 보강. 1.5 는 stopgap 으로 stdout 패턴 스캐닝.

### 2. 정기 실행 채널 (A + C 병행)

- **A. GitHub Actions scheduled** — `.github/workflows/infra-invariants-cron.yml`. 매일 1회 (UTC 03:00 = KST 12:00 권장 — 저트래픽 시간). 실패 시 GHA failure → 기존 alertmanager pipeline 진입.
- **C. prometheus textfile collector** — node-exporter 의 textfile collector 로 매트릭 expose. `aqts_infra_invariant_volume_perm_correct{volume="postgres_wal_archive"}` 형식. alertmanager rules 추가는 본 작업 범위에서 분리 (팀 2 운영 측면).

**B (systemd timer)** 미채택 사유: A 의 GHA scheduled 가 외부 (서버 외부) 검증 + audit trail 자동 보존 + 실패 시 PR 형태로 보고 가능 — 운영 부담 최소. C 의 textfile 은 pull 기반이라 prometheus 스크레이프 cycle 안에서 실시간 매트릭 갱신.

### 3. 6 그룹 테스트 구조 (OPS-022/026 패턴)

`backend/tests/test_check_infra_invariants.py` ≥ 18 tests:

1. 유효 통과
2. 위반 검출 (각 카테고리 1 케이스 ≥ 5)
3. 오탐 방지
4. 파서 견고성 (`docker inspect` 출력 변형, JSON key 누락, 빈 결과)
5. 실제 환경 회귀 (단, 일부 카테고리는 docker daemon / production DB 필요 — Ask #1~#3 참조)
6. `main()` 진입 (argparse, `--category`, exit code)

OPS-022/026 의 패턴 답습 + 카테고리별 mock 패턴 추가.

### 4. CLI 인터페이스 (전체 동의)

```bash
python scripts/check_infra_invariants.py --all
python scripts/check_infra_invariants.py --category volume-permissions
python scripts/check_infra_invariants.py --category network-ip
python scripts/check_infra_invariants.py --category external-reach
python scripts/check_infra_invariants.py --category pg-archiver
python scripts/check_infra_invariants.py --category log-patterns
python scripts/check_infra_invariants.py --all --output-json
```

exit code 0/1/2 정합. `--output-json` 은 alertmanager / textfile collector 통합용.

### 5. Doc Sync 워크플로 등록 (log-patterns 만)

```yaml
- name: Run infra invariants check (log-patterns only)
  run: python scripts/check_infra_invariants.py --category log-patterns
```

다른 카테고리 (volume-permissions, network-ip, external-reach, pg-archiver) 는 docker daemon + production DB 필요 → Doc Sync 워크플로의 PR 단계에서는 정적 분석 가능 영역인 log-patterns 만. 나머지는 정기 실행 채널 (A + C) 에서 검증.

## 명확화 요청 (Ask)

### Ask #1 — `log-patterns` 카테고리의 PR 단계 강제 — 입력 source 정의

§5 의 Doc Sync 등록 시 `--category log-patterns` 는 docker logs 를 어디서 읽는가? 후보:

- **(A) GHA runner 의 docker daemon** — GHA 가 docker-in-docker 지원 시 `docker compose up -d` 후 logs 캡처. 단 GHA 무료 tier 의 standard runner 는 docker-in-docker 가 가능하나, 본 워크플로의 service container 기동에 추가 ~1분 + alertmanager 등 의존성 미충족.
- **(B) 사전 캡처된 로그 fixture** — `backend/tests/fixtures/docker-logs/` 에 production 에서 export 한 로그 샘플을 두고 PR 단계에서 그것을 검증. silent miss 패턴이 향후 production 에서 발생하면 fixture 를 갱신.
- **(C) PR 단계 미실행** — log-patterns 도 정기 실행 채널 (A + C) 로만 검증. Doc Sync 등록 자체를 생략.

**Pilot 권장**: **(C) + (B) 조합**. (C) 가 기본, (B) 는 unit test 로 패턴 매칭 로직 검증 (실제 production 로그 의존 없음). Doc Sync 워크플로에 등록할 만큼 가치 있는 PR-단계 검증 대상이 모호하므로 정기 실행만으로 충분.

만약 PR 단계 검증을 강제하고 싶다면 (A) — 하지만 service container 기동 시간이 GHA 무료 tier 비용 ~$0.01/run 발생 + 워크플로 시간 ~3분 증가. 비용/이익 trade-off 회신 부탁.

### Ask #2 — `pg_archiver` 카테고리의 production DB 의존성

`check_pg_archiver(pg_container)` 가 `pg_stat_archiver` view 를 SELECT — production / staging 의 실제 DB 가 필요. 단위 테스트에서는 mock SQL 결과로 검증 가능하나, 정기 실행 (A + C) 에서는 어떻게 접근?

후보:

- **(A) GHA scheduled workflow 가 SSH 로 production aqts-server 진입 + `docker exec aqts-postgres psql ...`** — 기존 deploy 워크플로 패턴 답습. SSH key + sudo 권한 GHA secret 화. 보안 노출 표면 증가.
- **(B) production aqts-server 의 systemd cron 이 매시간 SQL 실행 + 결과를 textfile collector 로 write** — prometheus 매트릭으로만 expose. GHA 는 매트릭만 풀하여 검증 (또는 alertmanager rule 만 등록).
- **(C) prometheus 의 postgres_exporter 가 이미 `pg_stat_archiver` 매트릭 노출 — alertmanager rule 만 추가** — 신규 cron 불필요. 기존 매트릭 reuse.

**Pilot 권장**: **(C)**. postgres_exporter (이미 production 에 배포됨, `monitoring/prometheus/scrape-configs/postgres.yml`) 의 `pg_archiver_failed_count` / `pg_archiver_last_archived_age_seconds` 매트릭이 이미 expose 되어 있는지 확인 부탁. 있다면 alertmanager rule 만 추가하면 충분 — 본 검사기는 unit test (mock SQL 결과) 만 보유.

postgres_exporter 미사용 시 (B) — production cron + textfile collector. (A) 의 SSH 보안 표면은 회피.

### Ask #3 — `external-reach` 카테고리의 mock 전략

`check_external_reach(container, target_host, target_port)` 는 다른 컨테이너에서 `getent hosts` + `nc -zv` 를 실행 — production 환경의 실제 네트워크 의존. unit test 에서는 어떻게 mock?

후보:

- **(A) `docker exec` subprocess 호출을 monkeypatch** — `unittest.mock.patch("subprocess.run")` 로 stdout/exit code 를 제어. mock 작성 비용 + 실제 네트워크 동작 미검증.
- **(B) docker-compose-based integration test (별도 mark)** — `pytest -m integration` 으로 분리. 실제 service container 기동 후 검증. CI 무료 tier 비용 + 기동 시간.
- **(C) docker-py SDK 를 모듈 인터페이스로 추상화 + mock object 주입** — 모듈 내부에 `class DockerExecutor` 같은 인터페이스 + 테스트에서 fake 주입. 마지막은 OPS-022/026 패턴.

**Pilot 권장**: **(C)**. OPS-022/026 의 `read_grype_yaml` 처럼 외부 의존성을 인터페이스로 추상화 + 테스트에서 fake 주입. 단위 테스트는 fake 로 빠르게, 통합 테스트 (선택) 는 실제 docker 로 (별도 mark).

## 작업 일정

| 단계 | 일정 | 비고 |
|---|---|---|
| 본 합의 응답 (Ask #1~#3 명확화) | 즉시 발송 | |
| 리드 회신 수신 | 2026-04-29 (W1 마감) | Ask #1~#3 답변 |
| 자매 메일 정합 확인 (팀 2 backfill audit + 리드 §14.3) | 2026-04-29~30 | 팀 2 카테고리 분담 결과 + §14.3 정책 cite 위치 확정 |
| `scripts/check_infra_invariants.py` 핵심 스캐너 + 회귀 테스트 ≥18 | 2026-05-04 ~ 2026-05-12 | W1 종료 후, OPS-027 (W2) 보다 후순위 |
| Doc Sync (log-patterns) + GHA scheduled (A) + textfile collector (C) 통합 | 2026-05-13 ~ 2026-05-20 | Stage 2 Exit + 1주 마진 |
| **머지 마감** | 2026-05-13 (핵심 스캐너) / 2026-05-20 (운영 통합) | 위임 메일 §"응답 기한" 일정 |

## 응답 기한

**2026-04-29 (수) W1 종료 시점** — Ask #1~#3 회신.

미응답 시 fallback:
- Ask #1 → (C) Doc Sync 미등록 + (B) 단위 테스트 fixture
- Ask #2 → postgres_exporter 매트릭 reuse 시도, 미발견 시 (B) 서버 cron + textfile
- Ask #3 → (C) DockerExecutor 추상화 + fake 주입

본 fallback 으로 5/13 핵심 스캐너 마감 가능.
