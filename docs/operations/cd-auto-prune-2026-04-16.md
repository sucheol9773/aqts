# CD 자동 디스크 정리 · 호스트 디스크 알림 (2026-04-16)

## 0. 배경과 범위

Commit 4 (`507e26d`) 배포가 완료된 2026-04-16 UTC 19:00 전후, 단일-VM GCP 호스트(aqts-server)의 루트 디스크 사용률이 72.9% → 49.4% 로 하락했다. 이 변동 과정에서 다음 두 관측이 서로 맞지 않는다는 점이 확인됐다.

| 관측 | 값 |
|---|---|
| `docker image prune -a -f --filter "until=24h"` 공식 리포트 | `Total reclaimed space: 3.241GB` |
| 같은 시점 `df -h /` 변동 (usage %) | 72.9% → 49.4% (48GB boot disk 기준 약 **11GB** ↓) |
| 격차 | 약 **7.9GB** (2.4배) |

이 격차는 은폐된 장애가 아니라 **Docker CLI 의 `prune` 리포트가 overlay2 diff blocks · containerd content store 중복분을 manifest-level 에서 집계하지 않는** 구조적 under-report 로 수렴했다 (§1). 단, 이 사건은 동시에 **호스트 디스크 지표가 Prometheus 에 전혀 없다** 는 관측 공백을 드러냈다. node_exporter 가 배포되지 않았으므로 "80% 도달 전" 을 감지할 외부 경로가 없었다.

본 문서는 관측 공백 해소와 CD 파이프라인의 자동 정리 단계를 동시에 도입한 **Step A** 의 설계 근거, 변경 내용, 파라미터 선택, 회귀 방어선, 롤백 영향을 기록한다. 병행 예정 작업(Step B: APT 33건 + 커널 재부팅, Step C: `unattended-upgrades` 자동화)은 본 문서 범위 외다.

## 1. 7.9GB 격차의 원인: Docker CLI manifest-level under-report

### 1.1 관찰된 상태

Commit 4 배포 직후 다음 값이 동시에 관측됐다 (운영자 인터랙티브 세션).

- `docker system df -v` — `backend` 이미지 10개가 각각 823.5MB UNIQUE 로 집계 (재사용 레이어 제외).
- `landscape-sysinfo.cache` Modify 시각 (19:00:09 UTC) = MOTD 헤더 시각 (19:00:09 UTC) → 72.9% 는 **LIVE 값** 임이 확인됨. stale-cache 가설(Hypothesis B) 은 기각.
- `docker ps` — `aqts-backend`, `aqts-scheduler` 모두 `sha-507e26d` digest 에 고정. `latest` 태그는 동일 IMAGE ID (`8d137aadf74e`) 로 가리킴.
- prune 직후 `df -h /` 는 49.4%, 총 변동 **~11GB**.

### 1.2 결론

격차 7.9GB 는 **Docker CLI 의 `prune` 공식 리포트가 다음 회수량을 포함하지 않는 것** 으로 수렴한다.

- overlay2 `diff` 하위 디렉터리의 unreferenced 블록 — manifest 가 사라진 직후 GC 타이밍에 따라 지연 회수.
- containerd content store (`/var/lib/containerd/io.containerd.content.v1.content/blobs/sha256/…`) 내부의 중복 blob — `prune` 은 manifest graph 참조 감소분만 보고하므로, 같은 레이어가 여러 태그를 통해 참조됐다가 끊어지는 transitive 회수는 단일 리포트 숫자에 누적되지 않는다.

따라서 "격차 7.9GB" 는 **새로운 장애 원인이 아니라 리포팅 경로의 계측 결손** 이다. 장기적 대응은 (a) 호스트 파일시스템 지표를 Prometheus 에 도입하여 CLI 리포트에 의존하지 않는 관측점을 만들고, (b) CD 파이프라인이 자동으로 오래된 이미지를 prune 하도록 일상화하여 Docker 의 under-report 자체를 운영 리스크로 만들지 않는 것이다. Step A 는 정확히 이 두 축을 동시에 설치한다.

### 1.3 기각된 가설

- **Hypothesis B — MOTD stale cache**: `landscape-sysinfo.cache` 의 Modify(19:00:09) 가 MOTD 헤더의 "as of Wed Apr 15 19:00:09 UTC 2026" 과 정확히 일치했으므로, 72.9% 는 stale 캐시가 아니라 로그인 시점의 LIVE 값이었다. 본 가설은 사건 초기 추론으로 제안되었다가 관측으로 기각됐으며, "추론 ≠ 확정" 규칙(CLAUDE.md "오류 수정 시 관찰 우선 원칙") 의 직접 적용 사례로 기록한다.

## 2. Step A 설계

### 2.1 구성 요소

| # | 계층 | 산출물 | 역할 |
|---|---|---|---|
| 1 | 호스트 지표 | `node-exporter` service (`docker-compose.yml`) | VM rootfs/메모리/CPU/네트워크 메트릭을 Prometheus 로 노출. |
| 2 | 스크랩 | `aqts-node-exporter` job (`prometheus.yml.tmpl`) | `node-exporter:9100` 을 15s 간격으로 수집, `host="${HOST_LABEL}"` 라벨 주입(기본값 `aqts-server`, entrypoint sed 로 렌더링). |
| 3 | 알림 | `aqts_host_system` group (`aqts_alerts.yml`) | 루트 디스크 2단 알림 (`AqtsDiskUsageHigh` warning ≥80%, `AqtsDiskUsageCritical` critical ≥90%) + node-exporter 스크랩 부재 알림. |
| 4 | 자동 정리 | `Post-deploy cleanup (prune old images)` step (`cd.yml`) | 배포 verify 녹색 확인 후 SSH heredoc 으로 `docker image prune -a -f --filter "until=48h"` 실행. |

### 2.2 파라미터 선택 근거

- **`until=48h`** — 롤백 창(window) 은 실무상 "직전 2회 배포" 로 운영한다. 일일 배포 빈도 1~3회 기준 48h 는 2~6회 분의 구 이미지를 로컬에 보존한다. GHCR 에서 직접 pull 하는 롤백 경로가 있으므로 로컬 이미지 삭제가 롤백 불가능을 만들지는 않지만, 네트워크 장애가 겹치는 worst case 방어선이다. `until=24h` 로 짧혔을 때 발생하는 edge case (단일 날짜에 3~5회 배포 + 실패 롤백 필요) 를 회피한다.
- **`severity=warning` ≥ 80%, `for: 10m`** — GCP e2-standard-2 의 48GB boot disk 에서 80% = 38.4GB 사용. 이 선에서 경고가 울리면 다음 배포 전 운영자 개입 윈도우(수 시간) 를 확보한다. 10분 지연은 다운로드/빌드 스파이크 같은 일시적 점유를 흡수한다.
- **`severity=critical` ≥ 90%, `for: 2m`** — 90% = 43.2GB. 이 시점부터 postgres WAL archive, Docker image layer pull 이 실패 가능한 마진. 2분 지연은 오탐을 최소화하면서 "다음 배포까지 기다릴 수 없는" 수준의 긴급성을 표현한다.
- **`AqtsNodeExporterMissing`, `for: 10m`** — 메트릭 부재 자체가 블라인드이므로 10분을 넘으면 운영자 개입 대상. 본 알림이 울리는 동안 디스크 알림은 관측 공백 상태이므로 severity=warning 으로 고정 (메타 관측 알림).

### 2.3 왜 `docker compose run` / `docker exec` 가 아닌 `docker image prune` 인가

CD Post-deploy cleanup 스텝은 SSH heredoc 내부에서 실행되므로 `docker compose run` / `docker exec -i` 사용 시 §4.7/§4.8 회귀가 재현된다. `docker image prune` 은 stdin 을 attach 하지 않는 one-shot 명령이지만, 방어적으로 `</dev/null` 을 명시하여 장래 변경에 대해서도 부모 heredoc fd 0 상속을 차단한다.

## 3. 변경 내용

### 3.1 `docker-compose.yml`

`node-exporter` service 추가 (prom/node-exporter:v1.8.2). 핵심 옵션:

- `--path.rootfs=/host` — 모든 filesystem/proc/sys 조회를 `/host` 하위로 격리.
- `--collector.filesystem.mount-points-exclude` / `fs-types-exclude` — Docker overlay, containerd, tmpfs, proc 등을 제외하여 `node_filesystem_*{mountpoint="/"}` 가 **호스트 루트만** 반환.
- `/:/host:ro,rslave` 바인드 — read-only 로 루트 전체 마운트. `pid: host` 는 필요 최소 원칙으로 **도입하지 않음** (디스크/네트워크 메트릭이 일차 목적이며 pid 공유는 컨테이너 격리 완화).
- 포트 `127.0.0.1:${NODE_EXPORTER_PORT:-9100}:9100` — 외부 노출 금지. Prometheus 는 compose 내부 DNS 로 접근.

### 3.2 `monitoring/prometheus/prometheus.yml.tmpl` (+ `docker-compose.yml` prometheus entrypoint)

기존 `prometheus.yml` 을 `prometheus.yml.tmpl` 로 전환하고 `host` 라벨을 `${HOST_LABEL}` 플레이스홀더로 둔다. Prometheus 는 `static_configs` 의 라벨 값에 환경변수 확장을 지원하지 않고(`external_labels` 는 `--enable-feature=expand-external-labels` 를 켜도 `remote_write`/Alertmanager 송신 경로에만 적용되고 rule annotation 의 `$labels.host` 렌더링에는 기여하지 않음), 하드코딩은 CLAUDE.md "하드코딩 절대 금지" 에 어긋나므로, `docker-compose.yml` 의 prometheus service 에 alertmanager 와 동일한 entrypoint sed 렌더링 파이프라인을 도입했다.

- `environment: HOST_LABEL: ${HOST_LABEL:-aqts-server}` — 단일 VM 기본값.
- `entrypoint` 에서 `sed 's|${HOST_LABEL}|'"$HOST_LABEL"'|g' /etc/prometheus/prometheus.yml.tmpl > /tmp/prometheus.yml` 로 치환 후 `exec /bin/prometheus --config.file=/tmp/prometheus.yml …` 실행.
- 원본 이미지 CMD 의 `--storage.tsdb.path=/prometheus`, `--web.console.libraries`, `--web.console.templates` 는 entrypoint 오버라이드 시 소실되므로 새 entrypoint 에서 명시 재주입.
- `aqts-node-exporter` job 추가. `instance` 라벨은 Prometheus 가 자동 주입하고, 별도로 `host="${HOST_LABEL}"` 를 주입하여 알림 규칙 매칭과 메시지 렌더링(`{{ $labels.host }}`)을 안정화한다.
- 멀티 호스트 확장 시 각 서버의 `.env` 에서 `HOST_LABEL` 만 바꾸면 된다 (프로메테우스 설정 diff 불필요).

### 3.3 `monitoring/prometheus/rules/aqts_alerts.yml`

`aqts_host_system` group 신설. 3개 알림:

- `AqtsDiskUsageHigh` — `(size - avail) / size > 0.80`, `for: 10m`, warning.
- `AqtsDiskUsageCritical` — 같은 식 > 0.90, `for: 2m`, critical.
- `AqtsNodeExporterMissing` — `absent(up{job="aqts-node-exporter"} == 1)`, `for: 10m`, warning.

`fstype!~"tmpfs|overlay|devtmpfs|squashfs|nsfs"` 필터는 compose 측 scrape 필터와 2중 방어선을 이룬다 (한 쪽이 실수로 풀려도 다른 쪽이 막음).

### 3.4 `.github/workflows/cd.yml`

`Post-deploy verification` 다음, `Rollback on failure` 이전에 `Post-deploy cleanup (prune old images)` 스텝 삽입. `if: success()` 로 verify 가 녹색일 때만 실행. 실패 은폐 방지를 위해:

- `|| true` 로 prune 실패를 workflow 실패로 승격시키지 않는다 (cleanup 실패가 이미 성공한 배포를 rollback 시키면 안 됨).
- `df -h /` 전후 출력으로 실 회수량을 Actions 로그에 보존.
- `</dev/null` 로 stdin 격리. SSH heredoc `bash -s` 의 부모 fd 0 소진 위험(§4.7/§4.8) 을 원천 차단.

### 3.5 `.env.example`

두 엔트리 추가:

- `NODE_EXPORTER_PORT=9100` — `docker-compose.yml` 의 `${NODE_EXPORTER_PORT:-9100}` 와 1:1 대응.
- `HOST_LABEL=aqts-server` — prometheus entrypoint 에서 `prometheus.yml.tmpl` 의 `${HOST_LABEL}` 플레이스홀더를 치환하는 값. 멀티 호스트 확장 시 호스트별로 다르게 설정.

## 4. 회귀 방어선 (Wiring Rule 검증)

본 변경은 RBAC / 공급망 / 알림 파이프라인 Wiring Rule 과 동일한 "정의 ≠ 적용" 패턴을 가지며, 다음 wiring 이 모두 성립해야 완결된다. 배포 후 수동 점검 체크리스트로 사용한다.

| 레이어 | 검증 방법 | 통과 기준 |
|---|---|---|
| node-exporter 기동 | `docker compose ps node-exporter` | `running (healthy)` |
| 템플릿 렌더링 | `docker compose exec prometheus cat /tmp/prometheus.yml \| grep 'host:'` | `host: "aqts-server"` (또는 env 로 주입된 값), 리터럴 `${HOST_LABEL}` 가 남지 않음 |
| scrape 성공 | `curl -s http://localhost:9090/api/v1/targets` | `aqts-node-exporter` job 이 `up=1` |
| 메트릭 노출 | `curl -s http://localhost:9100/metrics \| grep -c '^node_filesystem_size_bytes'` | > 0 |
| **rule_files glob 로딩 (신규)** | `curl -s http://localhost:9090/api/v1/rules \| python3 -c "import sys,json; print(len(json.load(sys.stdin)['data']['groups']))"` | ≥ 1 (아래 silent miss 회귀 방어) |
| 알림 규칙 로드 | Prometheus UI `/rules` → `aqts_host_system` 그룹 | 3개 rule 모두 `ok` |
| CD prune 실행 | Actions UI 의 `Post-deploy cleanup` step | `df -h /` before/after 출력 + prune 결과 텍스트 존재 |

### 4.1 Silent Miss 회귀 사례 — `rule_files` 상대 경로 (2026-04-16)

Step A 초판 배포 후 검증에서 `aqts_host_system` 그룹이 Prometheus `/api/v1/rules` 에 전혀 나타나지 않았다. 관찰:

- `docker compose ps prometheus`: `Up 16 minutes (healthy)` — 재생성 성공.
- `/tmp/prometheus.yml` 1651B, entrypoint sed 로 `host: "aqts-server"` 정상 주입.
- `Loading configuration file` / `Completed loading ... rules=61.491µs` — config 로드 에러 없음.
- `/etc/prometheus/rules/aqts_alerts.yml` 30KB 로 마운트 정상.
- `curl .../api/v1/rules`: `{"data":{"groups":[]}}` — **0개 그룹**.
- `docker compose exec prometheus ls /tmp/rules/`: `No such file or directory`.
- `docker compose exec prometheus pwd`: `/prometheus`.

원인: Prometheus 는 `rule_files:` 의 상대 경로 glob 을 **config 파일 디렉터리 기준**으로 해석한다. 본 커밋에서 config 렌더링 위치를 `/etc/prometheus/prometheus.yml` → `/tmp/prometheus.yml` 로 옮기면서 `rules/*.yml` 이 `/tmp/rules/*.yml` 로 resolve 되어 빈 glob 을 반환했다. 빈 glob 은 Prometheus 가 에러로 처리하지 않으므로 "config 로드 성공 + 규칙 0개 + 에러 없음" 의 silent miss 경로로 빠졌다.

해결: `prometheus.yml.tmpl` 의 `rule_files` 를 절대 경로 `/etc/prometheus/rules/*.yml` 로 변경. 렌더링 위치와 무관하게 컨테이너 내 실 마운트 위치를 직접 참조한다.

회귀 방어선: 위 표의 "rule_files glob 로딩" 행을 영구 체크포인트로 승격. 배포 후 이 수치가 0 이면 같은 은폐 회귀가 발생한 것으로 간주한다.

검증 중 하나라도 실패하면 Silence Error 의심 원칙에 따라 "메트릭이 없어서 알림이 조용히 동작 안 함" 경로로 빠지지 않았는지 점검한다. 특히 §2.1-3 의 scrape 단계가 실패하면 §2.1-4 의 CD prune 은 정상 실행되지만 임계 관찰은 실효적으로 꺼진 상태다. 두 축은 독립이며 한쪽의 성공이 다른 쪽의 건강을 증명하지 않는다.

## 5. 롤백 영향

본 변경은 순수한 추가(additive) 변경이며 기존 서비스 경로에 영향을 주지 않는다.

- `node-exporter` service 는 compose 의 신규 service 다. 기존 backend/scheduler 의 depends_on 에 포함되지 않으므로 기동 실패가 다른 서비스 기동을 블록하지 않는다.
- Prometheus 는 `rule_files: ["/etc/prometheus/rules/*.yml"]` 로 로드하지만, `aqts_host_system` 그룹의 PromQL 이 에러인 경우에도 기존 그룹은 영향받지 않는다 (Prometheus 는 그룹 단위로 독립 평가).
- CD `Post-deploy cleanup` 스텝은 `if: success()` + `|| true` 의 이중 가드로 실패 전파를 차단한다.

롤백 절차 (이례적으로 본 변경을 되돌려야 할 때):

1. `docker-compose.yml` 의 `node-exporter` block 및 prometheus service entrypoint 블록 제거, `command:` + `./monitoring/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro` 볼륨 마운트로 복원 → `docker compose down node-exporter && docker compose up -d prometheus`
2. `prometheus.yml.tmpl` → `prometheus.yml` 로 되돌리고 `${HOST_LABEL}` 을 리터럴 값으로 교체 → Prometheus reload (`docker compose kill -s HUP prometheus`)
3. `aqts_alerts.yml` 의 `aqts_host_system` group 제거 → 같은 reload
4. `cd.yml` 의 `Post-deploy cleanup` step 제거 — 다음 배포에 반영
5. `.env.example` 에서 `NODE_EXPORTER_PORT`, `HOST_LABEL` 제거

롤백 자체로 서비스 중단은 없다 (관측/정리 기능만 축소).

## 6. Runbook — 디스크 알림 대응

### 6.1 AqtsDiskUsageHigh (warning, ≥80%)

1. `ssh aqts-server 'df -h /'` 로 실제 값 확인 (오탐 배제).
2. `ssh aqts-server 'docker system df'` — image/container/volume/build cache 상위 점유 확인.
3. `ssh aqts-server 'du -sh /var/lib/docker/* 2>/dev/null \| sort -h'` — overlay2/containers 디렉터리 크기 비교.
4. 다음 CD 실행이 자동 prune 하므로 배포 예정이 있으면 관찰. 배포 전 수동 prune 이 필요하면:
   ```bash
   ssh aqts-server 'docker image prune -a -f --filter until=48h'
   ```
5. 회수가 부족하면 unused volume / build cache 도 별도 정리 (`docker volume prune`, `docker builder prune`).

### 6.2 AqtsDiskUsageCritical (critical, ≥90%)

1. 즉시 `docker image prune -a -f` (필터 없이 실행 — 단, 현재 컨테이너가 사용 중인 이미지는 Docker 가 보호).
2. `docker volume prune -f` — 의존 볼륨은 postgres/mongodb/redis 가 사용 중이므로 영향 없음.
3. `journalctl --vacuum-size=200M` — 시스템 저널 압축.
4. `apt clean` — APT 캐시 제거.
5. 위 모두 실행 후에도 90% 이상이면 postgres WAL archive / db-backup 볼륨을 직접 확인:
   ```bash
   docker exec aqts-db-backup du -sh /backups
   docker exec aqts-postgres du -sh /var/lib/postgresql/wal_archive
   ```
6. 장기적으로 boot disk 증설 (50GB → 100GB) 을 고려.

### 6.3 AqtsNodeExporterMissing (warning)

1. `docker compose ps node-exporter` — `running (healthy)` 아니면 재시작 (`docker compose up -d node-exporter`).
2. `curl -s http://localhost:9100/metrics | head -5` — 컨테이너 재기동 후 메트릭 노출 확인.
3. Prometheus UI `/targets` 에서 scrape error 메시지 확인. 네트워크 DNS (`node-exporter:9100`) 이슈면 compose network 점검.

## 7. 후속 작업 (Step B / C, 본 커밋 범위 외)

- **Step B — APT 33건 + 커널 재부팅**: KST 15:40 이후 유지보수 창에서 수동 수행. 본 커밋과 독립.
- **Step C — `unattended-upgrades` 자동화**: LIVE 전환 전 1개월 관찰 후 도입. 재부팅 필요 패키지에 대한 별도 정책 수립 필요.
- **(선택) promtool 정적 검증**: `doc-sync-check.yml` 에 `promtool check rules` 를 추가하여 PR 단계에서 PromQL 파싱 오류를 잡는 방어선. 본 커밋 범위 외로 남겨둔다 (Prometheus 컨테이너 필요).
- **(선택) post_deploy_smoke.sh 에 C6 추가**: node_exporter 메트릭 쿼리로 디스크 임계 초과를 smoke 단계에서도 assert. 본 커밋은 알림 경로만 설치하고 smoke 계약은 추가하지 않는다.

## 8. 참고

- `docs/operations/daily-report-regression-2026-04-08.md` §4.7–§4.8 — SSH heredoc stdin 소진 회귀. 본 커밋의 `</dev/null` discipline 근거.
- `docs/operations/phase1-demo-verification-2026-04-11.md` §10.14–§10.16 — 관측 레이어 silent miss 패턴 (block buffering, loguru % posarg). 본 커밋의 "정의 ≠ 적용" 인식 연장선.
- `CLAUDE.md` — "SSH Heredoc 에서 비대화형 원격 명령 작성 규칙", "코드 수정 시 Silence Error 의심 원칙", "알림 파이프라인 Wiring Rule".
- Prometheus 문서: <https://prometheus.io/docs/guides/node-exporter/>, <https://github.com/prometheus/node_exporter>
