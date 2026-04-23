# `check_vuln_ignore_parity.py` — 취약점 ignore 목록 parity 정적 검사기 — 2026-04-23

> **문서 번호**: OPS-022
>
> **목적**: `.grype.yaml` 과 `backend/.pip-audit-ignore` 의 CVE/GHSA 식별자 집합을 대조하여, 한쪽에만 등록된 항목이 의도된 단방향 예외가 아닌데도 조용히 silent miss 되는 회귀를 구조적으로 차단한다. CLAUDE.md §9 미해결 TODO "`.grype.yaml ↔ backend/.pip-audit-ignore` parity 정적 검사기 신설" 의 공식 산출물.

---

## 1. 배경

### 1.1 2026-04-22 lxml GHSA silent miss 회귀

`chore/pip-audit-ignore-lxml-xxe` (PR #25) 커밋에서 lxml `GHSA-vfmq-68hx-4jfw` 를 `backend/.pip-audit-ignore` 에만 추가하고 `.grype.yaml` 동일 엔트리를 누락했다. 결과:

1. PR #25 로컬 CI (`pip-audit` 만 실행되는 레이어) 는 **녹색 통과**.
2. PR #25 머지 후 main 브랜치의 `cd.yml` 에서 `anchore/scan-action@v6` (grype) 이 실행되자 `GHSA-vfmq-68hx-4jfw` 가 high severity 로 재판정되어 배포 블록.
3. 후속 `fix/grype-yaml-glibc-lxml-parity` 브랜치에서 `.grype.yaml` 병기 엔트리를 추가하여 해소.

### 1.2 근본 원인

`pip-audit` (Python 패키지 스캐너) 와 `grype` (컨테이너 이미지 스캐너) 는 **ignore 목록을 공유하지 않는다**:

- `pip-audit` 은 `backend/.pip-audit-ignore` 파일의 줄 단위 형식을 사용.
- `grype` 은 `.grype.yaml` 의 YAML `ignore:` 블록을 사용.
- 두 도구의 규칙 파일 포맷, 경로, 파서가 전혀 다르므로 한쪽만 업데이트한 커밋도 로컬에서는 문제 없이 통과한다.

그러나 현재 CD 파이프라인은 두 스캐너를 **다른 타이밍** 에 실행:

- `pip-audit` → CI (PR check, 머지 전)
- `grype` (`anchore/scan-action@v6`) → CD (main push 후, 이미지 빌드 후)

따라서 "한쪽만 업데이트한 PR" 은 PR 리뷰 시점의 체크를 통과하고, 배포 직전에야 실패가 드러난다.

### 1.3 전통적 방어선의 한계

- **수동 리뷰**: "두 파일을 같이 업데이트해 주세요" 는 PR 템플릿에 넣어도 강제력 없음. 리뷰어도 놓칠 수 있다.
- **PR 체크리스트**: 한 번 빠뜨리면 다음 리뷰어가 찾아내기 어렵다 (두 파일이 물리적으로 떨어져 있다).
- **문서화**: CLAUDE.md §5, §9 에 경고를 남겨도 *그 문서를 읽은 사람에게만* 작동한다. 신규 팀메이트가 합류하거나 agent 자동화가 들어올 때 재현 가능성이 되살아난다.

정적 검사기는 이 갭을 메운다 — 두 파일의 집합 차집합을 CI 에서 매 커밋마다 자동 대조하고, 의도된 단방향 예외는 명시적 마커로 인정한다.

---

## 2. 설계 목표

1. **두 파일의 식별자 집합을 대조**: `.grype.yaml` 의 `- vulnerability: <ID>` 와 `backend/.pip-audit-ignore` 의 선두 식별자를 같은 공간으로 끌어올려 집합 연산.
2. **상호 배타적 차집합을 error 로 판정**: `grype - pip_audit` 과 `pip_audit - grype` 양방향 모두.
3. **의도된 단방향 예외를 허용**: OS 패키지 CVE 는 `pip-audit` 이 감지하지 못하고, 순수 Python 패키지 취약점은 `grype` 가 감지하지 못한다. 각 방향에 대해 `grype-only` / `pip-audit-only` 라인 레벨 마커로 예외 허용.
4. **CI 강제**: Doc Sync 워크플로에 0 errors 로 편입하여 PR 단계에서 차단.
5. **외부 의존성 0**: PyYAML 같은 런타임 외 의존성 없이 stdlib 만으로 파싱. Doc Sync 워크플로는 `actions/setup-python` 만 거치므로 무의존성 설계 필수.
6. **테스트 하니스 동반**: Stage 2 (`test_check_bool_literals.py`) / Stage 3 (`test_check_rbac_coverage.py`) 와 동일한 6 그룹 구조.

---

## 3. 구현

### 3.1 파서 (`scripts/check_vuln_ignore_parity.py`)

정규식 기반 라인 파서 2 종:

```
_GRYPE_LINE = r"""^\s*-\s*vulnerability:\s*['"]?(?P<id>CVE-\d{4}-\d{4,7}|GHSA-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4})['\"]?"""
_PIP_AUDIT_LINE = r"^\s*(?P<id>CVE-\d{4}-\d{4,7}|GHSA-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4})\b"
```

- `.grype.yaml` 은 YAML 포맷이지만 `ignore:` 아래의 관심 라인이 모두 `- vulnerability: <ID>` 고정 패턴이라 정규식만으로 충분하다. PyYAML 을 도입하면 Doc Sync 워크플로에 신규 의존성이 추가되어 비용 대비 편익이 없다.
- `backend/.pip-audit-ignore` 는 `<ID>  # <만료일> <근거>` 형식이라 선두 토큰만 끌어오면 된다.
- 인용 변형 (`- vulnerability: "CVE-..."` / `'CVE-...'`) 도 수용하여 향후 YAML 스타일 변경에 강건.
- 주석 라인(`# ...`) 이나 섹션 헤더의 CVE-like 토큰은 구조적으로 match 되지 않는다 (`^\s*-\s*vulnerability:` 와 `^\s*<ID>\b` 패턴이 주석 시작을 허용하지 않음).

### 3.2 단방향 예외 마커

두 개의 키워드 토큰:

- `grype-only` : `.grype.yaml` 의 엔트리 라인에 포함되면 "pip-audit 에는 없는 것이 의도됨" 으로 인정.
- `pip-audit-only` : `backend/.pip-audit-ignore` 의 엔트리 라인에 포함되면 반대 방향으로 인정.

마커 정규식은 `\bgrype-only\b` / `\bpip-audit-only\b` 로 단어 경계만 확인한다. 라인 레벨 파서가 이미 벤더 엔트리 라인으로 한정되어 있으므로 토큰이 ID/키 안에 섞일 여지는 없다. 기존 인라인 주석 (`# <expiry> <근거>`) 에 키워드만 삽입하면 되어 기존 컨벤션을 깨지 않는다:

```yaml
  - vulnerability: CVE-2025-15281  # grype-only 2026-06-06
```

### 3.3 parity 판정 함수

```python
def check_parity(grype: dict[str, bool], pip_audit: dict[str, bool]) -> list[str]:
    errors = []
    for ident in sorted(set(grype) - set(pip_audit)):
        if not grype[ident]:
            errors.append(f"{ident}: .grype.yaml 에만 존재. ...")
    for ident in sorted(set(pip_audit) - set(grype)):
        if not pip_audit[ident]:
            errors.append(f"{ident}: .pip-audit-ignore 에만 존재. ...")
    return errors
```

`dict[str, bool]` 값은 `has_marker`. 정렬된 리스트를 반환하여 출력이 결정적이다 (CI 로그 diff 안정).

### 3.4 CI wiring

`.github/workflows/doc-sync-check.yml` 에 단일 스텝 추가:

```yaml
- name: Run vuln-ignore parity check
  run: python scripts/check_vuln_ignore_parity.py
```

`python scripts/check_*.py` 관용구로 기존 4 개 스텝(bool literals, doc-sync, RBAC, CD stdin, loguru style) 과 동일 표면으로 통합.

### 3.5 테스트 하니스 (`backend/tests/test_check_vuln_ignore_parity.py`)

Stage 2/3 과 동일한 6 그룹, 총 15 개 테스트:

1. **동일 ID 집합 — 통과 (2 tests)**: 기본 성공 경로 + 빈 파일.
2. **단방향 존재 + 마커 없음 — error (3 tests)**: grype-only / pip-audit-only 방향 각각, 복수 차집합 정렬 고정.
3. **단방향 존재 + 마커 = 허용 (4 tests)**: grype-only 마커, pip-audit-only 마커, 두 마커 독립 판정, 잘못된 방향 마커는 인정 안 됨.
4. **파서 오탐 방지 (3 tests)**: 인용 변형, 섹션 헤더 주석, 선두가 `#` 인 주석 라인의 CVE-like 토큰 무시.
5. **실제 저장소 회귀 고정 (1 test)**: 현재 `.grype.yaml` / `backend/.pip-audit-ignore` 가 parity 통과 상태 유지.
6. **main() 진입 경로 (2 tests)**: 실제 레포 CLI 실행 exit 0, 필수 파일 누락 시 exit 1.

테스트 하니스는 `importlib.util.spec_from_file_location` 으로 모듈을 명시 로드하고 `tmp_path` 로 fixtures 를 생성하는 기존 Stage 2/3 패턴을 그대로 재사용한다.

---

## 4. 초기 데이터 정합화

본 검사기를 도입하면서 기존 `.grype.yaml` 의 21 개 OS 패키지 / Python 인터프리터 CVE 엔트리가 pip-audit 에 없는 것이 정상임에도 parity 위반으로 보고되었다. 각 라인에 `grype-only` 키워드를 추가하여 의도된 단방향 예외임을 명시:

- glibc 8건 (CVE-2025-15281, CVE-2026-4046, CVE-2026-4437, CVE-2026-0915, CVE-2026-0861, CVE-2026-5450, CVE-2026-5358, CVE-2026-5928)
- libsqlite3-0 1건 (CVE-2025-7458)
- libldap-2.5-0 1건 (CVE-2023-2953)
- libexpat1 2건 (CVE-2025-59375, CVE-2026-25210)
- dpkg 2건 (CVE-2025-6297, CVE-2026-2219)
- libtasn1-6 1건 (CVE-2025-13151)
- ncurses 1건 (CVE-2025-69720)
- libnghttp2-14 1건 (CVE-2026-27135)
- Python 3.11 4건 (CVE-2026-4519, CVE-2025-13836, CVE-2026-4786, CVE-2026-6100)

변경 후 검사 결과:

```
vuln-ignore parity OK (grype=25, pip-audit=4, shared=4)
```

공유 4건: `GHSA-7gcm-g887-7qv7` (protobuf), `GHSA-jr27-m4p2-rc6r` (pyasn1), `GHSA-wj6h-64fc-37mp` (ecdsa), `GHSA-vfmq-68hx-4jfw` (lxml) — 모두 Python 패키지 CVE 로 양쪽 스캐너가 모두 감지하므로 양쪽에 등록.

---

## 5. 운영 가이드

### 5.1 새 CVE/GHSA 억제 시

**기본 원칙: 두 파일을 동일 커밋에서 병행 업데이트**. 다음 세 가지 경우를 나눠 판단:

1. **Python 패키지 취약점 (양쪽 스캐너 모두 감지)**: `.grype.yaml` 과 `backend/.pip-audit-ignore` 양쪽에 등록. 예: lxml, protobuf, pyasn1.
2. **OS 패키지 / 컨테이너 베이스 이미지 취약점 (grype 만 감지)**: `.grype.yaml` 에만 등록하되 라인에 `grype-only` 키워드 포함. 예: glibc, libsqlite3, Python 인터프리터 CPython.
3. **순수 Python 전용 취약점 (pip-audit 만 감지, 드물다)**: `backend/.pip-audit-ignore` 에만 등록하되 `pip-audit-only` 키워드 포함.

### 5.2 로컬 사전 검증

커밋 전 반드시:

```bash
python scripts/check_vuln_ignore_parity.py
```

예상 출력: `vuln-ignore parity OK (grype=N, pip-audit=M, shared=K)`.

### 5.3 실패 리포트 해석

검사기 실패 시 출력은 각 위반마다 한 라인:

```
CVE-2099-12345: .grype.yaml 에만 존재. backend/.pip-audit-ignore 에 추가하거나 grype 라인에 `# grype-only` 마커를 붙이세요.
```

메시지에 의도된 해결 경로(마커 추가 OR 병기 엔트리 추가) 가 명시되어 있다.

### 5.4 만료일 관리

본 검사기는 **만료일을 해석하지 않는다**. 만료일 관리는 CI 의 `pip-audit` 스텝이 TODAY 와 `<expiry>` 를 비교하여 `--ignore-vuln` 플래그 포함 여부를 결정하는 기존 경로가 담당한다 (backend/.pip-audit-ignore 헤더 참조). 본 검사기는 순수하게 "엔트리 존재 여부" 만 본다.

---

## 6. 검증

### 6.1 단위 테스트

```bash
cd backend && python -m pytest tests/test_check_vuln_ignore_parity.py -q
# 15 passed
```

### 6.2 레포 전체 CI 통과 조건

- Doc Sync 워크플로의 `Run vuln-ignore parity check` 스텝이 exit 0.
- 기존 5 개 정적 검사 스텝(bool literals, doc-sync, status SSOT, RBAC, CD stdin, loguru style) 은 본 PR 로 인해 변경되지 않아야 한다.

### 6.3 현재 저장소 상태

- 변경 전: `.grype.yaml=21 OS/Python CVE + 4 shared GHSA` / `pip-audit=4 shared GHSA` / 상호배타 21건 — **parity FAIL**.
- 변경 후: `.grype.yaml=25 (21 OS/Python marked grype-only + 4 shared) / pip-audit=4 shared` / 상호배타적 단방향은 모두 마커 보유 — **parity OK**.

---

## 7. 회귀 방어선 관점

본 검사기는 다음 silent miss 패턴에 대한 구조적 방어선이다:

1. **"한쪽만 업데이트" 실수**: 신규 CVE 를 한 파일에만 추가하고 잊음.
2. **"만료일만 갱신" 비대칭**: 만료일을 한 파일에서만 연장하여 다른 파일은 자동 무효화되는 경우. (※ 본 검사기 범위 밖 — 만료일은 pip-audit 측 파서가 담당. 하지만 엔트리 존재 자체는 본 검사기가 집행한다.)
3. **리팩토링 중 ID 누락**: CVE 식별자가 개정되어 GHSA 로 재태깅되거나 그 반대의 상황에서 한쪽만 따라가는 경우.
4. **agent 자동화 도입 시 규칙 학습 실패**: 신규 팀메이트 / LLM 기반 자동 PR 이 "두 파일 병행" 관습을 모를 때.

---

## 8. 관련 문서

- `CLAUDE.md §5` 2026-04-21/22 회귀 사례 목록
- `CLAUDE.md §9` 미해결 TODO (본 산출물로 해소)
- `agent_docs/development-policies.md §13` 공급망 보안 정책
- `docs/security/supply-chain-policy.md`
- OPS-017: `iter_python_files` SSOT 집약
- OPS-018: Dev 의존성 파일 분리
- OPS-019: `check_bool_literals.py` regex → AST 전환
- OPS-020: `check_rbac_coverage.py` 회귀 테스트 하니스

---

## 9. 팀 소유권

- **정상 상태**: 팀메이트 4 (Tests / Doc-Sync / Static Checkers) 소유.
- **2026-05-06 까지 임시 소유**: ADR-002 Pilot lockout 기간 중 리드 임시 소유. Pilot 해제 후 팀 4 에 인계.
- **향후 변경**: 규칙 조정 (마커 이름, 새 방향 마커 추가 등) 은 팀 4 영역. 실제 `.grype.yaml` / `backend/.pip-audit-ignore` 엔트리 추가는 공급망 보안 변경이므로 팀 3 (Security) 이 주도하고 팀 2 (Ops) 가 CD 파이프라인 관점에서 리뷰.
