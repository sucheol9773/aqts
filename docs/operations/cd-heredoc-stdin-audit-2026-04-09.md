# CD Heredoc stdin 소진 경로 전수 감사 (2026-04-09)

## 1. 배경

2026-04-09 에 두 사이클 연속으로 SSH heredoc stdin 소진이 재현되며 CD Step 5c 이후 단계가 조용히 은폐됐다 (§4.7 `docker exec -i`, §4.8 `-T` 없는 `docker compose run`). 두 사고 이후 다음 세 가지 대응이 커밋되었다:

- `8fcd6c6` — `docker exec -i` → `docker exec ... </dev/null`
- `43b388b` — `docker compose run ...` → `docker compose run -T ... </dev/null`
- `5cacdcf` — `scripts/check_cd_stdin_guard.py` 정적 가드 + 19 개 회귀 테스트 + Doc Sync 등록

이 감사는 위 대응이 **충분한가**, 즉 `.github/workflows/` 와 `scripts/` 전체에서 동일한 fd 0 상속 은폐 경로가 추가로 존재하는지를 확인하기 위해 수행되었다.

## 2. 감사 범위

- `.github/workflows/*.yml` 전체
- `scripts/**/*.sh` 전체
- 검색 패턴: `ssh`, `docker exec`, `docker compose run`, `docker run`, `bash -c`, `kubectl exec`, `bash X.sh` / `sh X.sh` / `./X.sh`

## 3. 발견 — 카테고리별

### 3.1 Multi-line SSH heredoc (4 건, 모두 가드 범위 내)

`cd.yml` 의 네 heredoc 블록은 모두 `ssh -T ... bash -s << 'TAG'` 형태로 시작하며 정적 가드의 서브쉘 heredoc 판정 조건을 만족한다.

| Step | TAG | 범위 |
|---|---|---|
| Deploy to server (line 106) | `DEPLOY_SCRIPT` | Step 1 ~ Step 6 |
| Post-deploy verification (line 293) | `VERIFY_SCRIPT` | Health Check ~ smoke 호출 |
| Rollback (line 382) | `ROLLBACK_SCRIPT` | 이전 image 복원 경로 |
| Notify (line 455) | `NOTIFY_SCRIPT` | 배포 완료 알림 |

세 블록 모두 내부 `docker exec`, `docker compose run`, `docker run` 호출에 대해 Rule 1~4 가 적용된다. **현 상태: clean.**

### 3.2 Single-command SSH (1 건, 위험 없음)

`cd.yml:74`:
```
PREV_SHA=$(ssh -i ~/.ssh/gcp_key ${SERVER_USER}@${SERVER_IP} \
  "cd ~/aqts && git rev-parse --short HEAD 2>/dev/null || echo 'none'" | tail -1)
```

단일 명령 form (`ssh ... "cmd"`). heredoc 이 없으므로 stdin forward 이슈 원천 부재. 안전.

### 3.3 독립 실행 스크립트 (3 개, heredoc 바깥)

`scripts/deploy.sh`, `scripts/pre_deploy_check.sh`, `scripts/verify_deployment.sh` 는 모두 CI 가 아닌 운영자가 로컬/서버에서 직접 실행하는 스크립트이다. 이들 내부의 `docker exec` / `docker run` 호출은 CD heredoc 컨텍스트 밖에 있으므로 부모 fd 0 이 heredoc 이 아니다. Rule 1~4 는 heredoc 내부에만 적용되므로 이들은 flag 되지 않으며, 실제 위험도 없다.

### 3.4 잠재 갭 — heredoc 내부에서 호출되는 하위 스크립트 (1 건)

**`cd.yml:361` — `bash scripts/post_deploy_smoke.sh` (VERIFY_SCRIPT heredoc 내부)**

```
ssh -T ... bash -s <<VERIFY_SCRIPT
  ...
  bash scripts/post_deploy_smoke.sh        # ← 자식 bash 가 부모 fd 0 상속
  echo "✅ All verification checks passed"
VERIFY_SCRIPT
```

자식 `bash scripts/post_deploy_smoke.sh` 는 부모 heredoc 스트림을 fd 0 으로 상속한다. **현재** post_deploy_smoke.sh 내부의 `docker exec aqts-scheduler stat ...` 호출들은 모두 `-i` 가 없어 stdin 을 읽지 않으므로 실제 소진은 발생하지 않는다. 그러나 이는 우연한 안전이며, smoke 스크립트에 누군가 `docker exec -i` 한 줄만 추가하는 순간 VERIFY_SCRIPT 의 `echo "✅ All verification checks passed"` 가 조용히 소진되고 post-deploy 단계가 clean exit 하며 §4.7 패턴이 재발한다.

기존 가드 Rule 1~4 는 `post_deploy_smoke.sh` 를 독립 스크립트로 보기 때문에 heredoc 컨텍스트 밖으로 판정해 검사하지 않으며, 따라서 이 갭을 잡지 못한다.

## 4. 대응 — 이중 방어선

### 4.1 Layer A (호출 지점 격리)

`cd.yml:361` 을 다음과 같이 수정해 상속 사슬을 즉시 끊는다:

```
bash scripts/post_deploy_smoke.sh </dev/null
```

`</dev/null` 은 자식 bash 의 fd 0 을 /dev/null 에 고정한다. smoke 스크립트 내부 어떤 자식도 부모 heredoc 스트림을 읽을 수 없다. 이 격리는 smoke 스크립트의 **장래 변경과 무관하게** 성립한다.

### 4.2 Layer B (정적 가드 확장 — Rule 5)

`scripts/check_cd_stdin_guard.py` 에 Rule 5 를 추가한다. heredoc 내부에서 `bash X.sh` / `sh X.sh` / `./X.sh` 형태의 하위 스크립트 호출이 발견되면, 같은 논리 라인에 `</dev/null`, `< FILE`, `<<<`, 또는 상단 파이프(`|`) 입력이 있어야 통과한다. 셋 중 아무것도 없으면 ERROR.

이 규칙은 "heredoc 내부에서 호출되는 모든 하위 shell 스크립트는 stdin 을 명시적으로 격리해야 한다" 를 기계적으로 강제한다. Layer A 의 수정을 되돌리거나, 장래에 VERIFY_SCRIPT 안에 다른 `bash X.sh` 가 추가될 때 모두 이 규칙에 걸린다.

### 4.3 회귀 테스트 (`TestRule5InheritedScriptInvocation`, 8 개)

- `test_detects_bash_script_without_redirect` — VERIFY_SCRIPT 안의 `bash scripts/post_deploy_smoke.sh` (without redirect) 가 flag 된다
- `test_detects_sh_script_without_redirect` — `sh scripts/run.sh` 도 동일
- `test_detects_dot_slash_script_without_redirect` — `./scripts/run.sh` 도 동일
- `test_passes_with_dev_null_redirect` — `bash X.sh </dev/null` 은 통과
- `test_passes_with_pipe_input` — `echo arg | bash X.sh` 는 파이프로 stdin 치환되므로 통과
- `test_passes_with_file_redirect` — `bash X.sh < /tmp/input.txt` 도 통과
- `test_no_false_positive_outside_heredoc` — 일반 CI 단계의 독립 `bash X.sh` 는 오탐 없음
- `test_no_false_positive_bash_dash_s_heredoc_start` — `bash -s << TAG` heredoc 시작 라인 자체는 flag 되지 않음

## 5. 비적용 대상 — 의도적 범위 제한

다음은 이론적 위험이 있으나 현실적 근거로 Rule 5 범위에서 제외했다.

- **Python/Node 스크립트 호출** (`python3 X.py`, `node X.js`): 이론적으로 fd 0 을 상속하지만, CD heredoc 안에서 이런 호출이 docker 관련 작업을 수행하는 경우는 없다. Rule 5 가 확장이 필요해지면 해당 시점에 관측 기반으로 규칙을 추가한다 (추론만으로 확장하지 않는다).
- **비-`.sh` 확장자 스크립트 호출**: 동일한 이유. 실제 관측된 위험 클래스 = shell 스크립트.
- **재귀 하위 스크립트 호출**: `post_deploy_smoke.sh` 가 다른 스크립트를 호출하는 경우. 현재는 존재하지 않으며, Layer A 의 `</dev/null` 이 적용되어 있으면 어떤 재귀 호출도 이미 격리된 fd 0 만 상속받는다.

## 6. 강제 검사 절차

| 시점 | 절차 |
|---|---|
| 로컬 개발 | `python scripts/check_cd_stdin_guard.py` |
| PR 검증 | `.github/workflows/doc-sync-check.yml` 의 `Run CD stdin guard check` step (scripts/** 또는 .github/workflows/** 수정 시 자동 트리거) |
| 회귀 고정 | `backend/tests/test_check_cd_stdin_guard.py::TestRule5InheritedScriptInvocation` (8 tests) + `TestRepositoryClean::test_current_repo_passes` |

## 7. 결론

- **Active 취약점 없음** — 감사 시점 기준 .github/workflows/ 와 scripts/ 모두 Rule 1~4 를 위반하지 않으며, 실제 heredoc stdin 소진 경로는 존재하지 않는다.
- **잠재 갭 1 건 식별** — `bash scripts/post_deploy_smoke.sh` 가 VERIFY_SCRIPT 내부에서 `</dev/null` 없이 호출되고 있었다. 실제 소진은 없으나 장래 회귀 경로.
- **이중 방어선 적용** — Layer A (호출 지점 `</dev/null`) + Layer B (Rule 5 정적 가드) 커밋 완료.

§4.7/§4.8 회귀의 "플래그 중심 → 기본값 중심" 일반화 원칙이 이번에도 적용됐다: "`-i` 금지" (플래그) 가 아니라 "자식이 fd 0 을 읽을 수 있는가" (상속 관계) 로 규칙을 일반화해야 재발을 막을 수 있다.

## 참고

- `docs/operations/daily-report-regression-2026-04-08.md` §4.7, §4.8, §4.9, §4.10, §4.11
- CLAUDE.md "SSH Heredoc 에서 비대화형 원격 명령 작성 규칙"
- `scripts/check_cd_stdin_guard.py` (Rule 1~5)
- `backend/tests/test_check_cd_stdin_guard.py` (27 회귀 테스트)
