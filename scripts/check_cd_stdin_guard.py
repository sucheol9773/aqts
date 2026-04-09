#!/usr/bin/env python3
"""CD 스크립트 stdin 소진 방지 정적 검사기.

정책: CLAUDE.md "SSH Heredoc 에서 비대화형 원격 명령 작성 규칙"
배경: ``docs/operations/daily-report-regression-2026-04-08.md`` §4.7, §4.8

검사 대상:
  - ``.github/workflows/*.yml``
  - ``scripts/**/*.sh``

검사 범위: 위 파일들 안에서 **서브쉘 heredoc** 으로 시작된 블록 내부만.
구체적으로 ``bash -s``, ``sh -s``, ``ssh ... bash``, ``ssh ... sh`` 같은
"원격/서브쉘을 stdin 으로 먹이는" 명령과 같은 줄에 ``<< [-]TAG`` 또는
``<< '[-]TAG'`` heredoc 시작이 등장하면 해당 블록에 들어간 것으로 본다.
종료 조건은 TAG 가 단독으로 등장하는 줄. 본 맥락 바깥(예: 일반 CI 단계의
``docker run`` , 로컬 pre-deploy 체크 스크립트의 독립 ``docker run``)은 stdin
소진의 위험이 원천 없으므로 검사하지 않는다.

차단 패턴 (heredoc 내부에서 모두 ERROR, exit 1):
  1. ``docker exec -i`` / ``docker exec --interactive``
     → ``docker exec ... </dev/null`` 로 교체.
  2. ``kubectl exec -i`` / ``kubectl exec --stdin``
     → ``kubectl exec ... </dev/null`` 로 교체.
  3. ``-T`` 없는 ``docker compose ... run ...`` (또는 구 표기 ``docker-compose``)
     → ``docker compose run --rm -T backend ... </dev/null``.
  4. ``-T`` 없는 전경 ``docker run ...``. ``-d``/``--detach`` 로 백그라운드 실행되는
     경우는 stdin 을 소비하지 않으므로 예외.
     → ``docker run --rm -T ... </dev/null``.
  5. heredoc 내부에서 ``bash X.sh`` / ``sh X.sh`` / ``./X.sh`` 형태의 하위 스크립트
     호출에 ``</dev/null`` 또는 ``< FILE`` redirect, 혹은 상단 ``|`` 파이프 입력이
     없는 경우. 자식 bash 가 부모 heredoc 의 fd 0 을 상속하면, 해당 스크립트
     내부 어딘가에 ``docker exec -i`` 같은 stdin 소비 라인이 추가되는 순간
     §4.7/§4.8 과 동일한 은폐 경로가 재생성된다. 호출 지점에서 상속 사슬을
     끊어 하위 스크립트의 **장래 변경으로부터 격리**한다.
     → ``bash scripts/X.sh </dev/null``.

근거: ``ssh -T ... bash -s << 'EOF'`` heredoc 안에서 실행된 자식 프로세스가
부모 bash 의 fd 0 을 상속받아 heredoc 의 잔여 라인을 모두 소진하면, 부모
bash 는 다음 라인을 읽으려다 EOF 를 만나 ``exit 0`` 으로 정상 종료한다.
``set -e`` 는 이 경로를 잡지 못하며, 외부 관찰(CI UI)로는 ``step ✓`` 로 보여
드리프트가 은폐된다. 2026-04-09 에 이 사고가 두 번 연속 재현되었다:
``docker exec -i`` (051c453/6500bcb/a48c4c8) 와 ``-T`` 없는 ``docker compose run``
(8fcd6c6). 두 경우 모두 scheduler 가 구 이미지로 고정된 채 새 이미지가
단 한 번도 기동되지 못했다.

Exit code:
  0  모든 파일이 통과
  1  하나 이상의 차단 패턴 발견

사용법:
  python scripts/check_cd_stdin_guard.py

본 스크립트는 ``.github/workflows/doc-sync-check.yml`` 에 등록되어 Doc Sync
워크플로에서 자동 실행된다.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TARGET_GLOBS: tuple[tuple[str, str], ...] = (
    (".github/workflows", "*.yml"),
    ("scripts", "*.sh"),
)

# ─────────────────────────────────────────────────────────────────────────
# 차단 패턴 정의
# ─────────────────────────────────────────────────────────────────────────
# 핵심: 각 패턴은 "한 논리 명령 안에서" 매칭되어야 한다. 백슬래시 라인
# 연속을 고려해 논리 라인 단위로 합친 뒤 검사한다.

# Rule 1: docker exec -i / --interactive
RE_DOCKER_EXEC_I = re.compile(
    r"\bdocker\s+exec\s+(?:[^|&;]*?\s)?(?:-i\b|--interactive\b)"
)

# Rule 2: kubectl exec -i / --stdin
RE_KUBECTL_EXEC_I = re.compile(
    r"\bkubectl\s+exec\s+(?:[^|&;]*?\s)?(?:-i\b|--stdin\b)"
)

# Rule 3: docker compose ... run ... (without -T)
# docker-compose (구 표기) 도 동일하게 차단.
RE_COMPOSE_RUN = re.compile(
    r"\bdocker(?:-|\s+)compose\b[^|&;]*?\brun\b[^|&;]*"
)

# Rule 4: docker run ... (without -T, 전경 실행)
RE_DOCKER_RUN = re.compile(
    r"\bdocker\s+run\b[^|&;]*"
)

# `-T` 또는 `--no-TTY` 검출 (compose run, docker run 공통)
RE_HAS_T_FLAG = re.compile(
    r"(?:^|\s)(?:-T\b|--no-TTY\b|--interactive=false\b)"
)

# `-d` 또는 `--detach` 검출 (docker run 예외 — 백그라운드 실행은 stdin 소비 없음)
RE_HAS_DETACH_FLAG = re.compile(
    r"(?:^|\s)(?:-d\b|--detach\b)"
)

# Rule 5: heredoc 내부에서 하위 shell 스크립트 호출.
# `bash foo.sh`, `sh foo.sh`, `./foo.sh`, `bash ./scripts/foo.sh` 등을 매치한다.
# 주의: 본 가드 스크립트 자체 (`check_cd_stdin_guard.py`) 와 혼동되지 않도록
# 확장자는 `.sh` 로 한정한다.
RE_SCRIPT_INVOKE = re.compile(
    r"(?:\b(?:bash|sh)\s+(?:-[A-Za-z]+\s+)*"
    r"(?P<script>\.?\.?/?[\w./-]+\.sh)\b"
    r"|(?:^|[\s;&|])(?P<script2>\./[\w./-]+\.sh)\b)"
)

# stdin 격리 redirect / 파이프 입력 검출. 아래 중 하나라도 있으면 OK:
#   `</dev/null`, `< somefile`, `| bash script.sh`, `<<< here-string`.
# 파이프는 스크립트 호출 "앞" 에 와야 하지만, 라인 전체에 `|` 가 있으면
# 보통 `cmd | bash script.sh` 형태이므로 단순 presence 검사로 충분하다.
RE_STDIN_ISOLATED = re.compile(
    r"(?:<\s*/dev/null\b|<\s+\S|<<<|\|)"
)

# heredoc 시작 검출: ``<< TAG`` / ``<<-TAG`` / ``<< 'TAG'`` / ``<< "TAG"``.
# 서브쉘 heredoc 만 관심 대상이므로 같은 줄에 ``bash``/``sh`` (``-s`` 포함/미포함,
# ``ssh ... bash`` 형태 포함) 가 등장해야 한다.
RE_HEREDOC_START = re.compile(
    r"<<\s*-?\s*(?P<quote>['\"]?)(?P<tag>[A-Za-z_][A-Za-z0-9_]*)(?P=quote)"
)
RE_SUBSHELL_HINT = re.compile(
    r"\b(?:bash|sh)\b(?:\s+-[^<\s]*)?(?:\s|$)"
)


# ─────────────────────────────────────────────────────────────────────────
# 유틸리티
# ─────────────────────────────────────────────────────────────────────────
def join_continuations(lines: list[str]) -> list[tuple[int, str]]:
    """백슬래시 라인 연속을 한 논리 라인으로 합친다.

    Returns:
        (first_line_number, joined_text) 튜플 리스트. 줄번호는 원본 파일의
        첫 번째 물리 줄 번호(1-based).
    """
    result: list[tuple[int, str]] = []
    buf: list[str] = []
    buf_start: int | None = None
    for idx, raw in enumerate(lines, start=1):
        stripped = raw.rstrip("\n")
        if buf_start is None:
            buf_start = idx
        # 라인 끝의 백슬래시는 제거하고 버퍼에 이어붙인다
        if stripped.endswith("\\"):
            buf.append(stripped[:-1])
            continue
        buf.append(stripped)
        result.append((buf_start, " ".join(buf)))
        buf = []
        buf_start = None
    if buf and buf_start is not None:
        result.append((buf_start, " ".join(buf)))
    return result


def strip_comment(text: str) -> str:
    """``#`` 이후의 주석을 제거한다. 문자열 리터럴 내부의 ``#`` 은 보존.

    bash/yaml 모두 ``#`` 을 주석 기호로 사용한다. 간단히 따옴표 추적 기반으로
    처리한다. quote escape 는 이 용도에서 중요하지 않으므로 근사 처리.
    """
    out: list[str] = []
    in_single = False
    in_double = False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            break
        out.append(ch)
        i += 1
    return "".join(out)


def check_line(text: str) -> list[str]:
    """한 논리 라인에서 발견된 차단 패턴을 문자열 리스트로 반환한다."""
    violations: list[str] = []

    # 주석 제거 후 검사
    code = strip_comment(text)

    # Rule 1: docker exec -i
    if RE_DOCKER_EXEC_I.search(code):
        violations.append(
            "`docker exec -i` / `--interactive` — 비대화형 CI 에서는 금지. "
            "`docker exec ... </dev/null` 로 교체."
        )

    # Rule 2: kubectl exec -i
    if RE_KUBECTL_EXEC_I.search(code):
        violations.append(
            "`kubectl exec -i` / `--stdin` — 비대화형 CI 에서는 금지. "
            "`kubectl exec ... </dev/null` 로 교체."
        )

    # Rule 3: docker compose run (without -T)
    # 여러 번 등장할 수 있으나 한 라인에 통상 1회. 매치 구간별로 검사.
    for m in RE_COMPOSE_RUN.finditer(code):
        segment = m.group(0)
        if not RE_HAS_T_FLAG.search(segment):
            violations.append(
                "`-T` 없는 `docker compose run` — heredoc stdin 을 소진할 수 있다. "
                "`docker compose run --rm -T ... </dev/null` 로 교체."
            )

    # Rule 4: docker run (without -T, not detached)
    # 주의: `docker run` 은 `docker compose run` 부분문자열로 매치되므로,
    # compose 가 앞서 나온 경우는 스킵한다.
    for m in RE_DOCKER_RUN.finditer(code):
        start = m.start()
        # 직전 토큰이 "compose" 인 경우는 rule 3 에서 처리된 것이므로 스킵
        prefix = code[:start].rstrip()
        if prefix.endswith("compose"):
            continue
        segment = m.group(0)
        if RE_HAS_DETACH_FLAG.search(segment):
            continue  # 백그라운드 실행은 stdin 소비 없음
        if not RE_HAS_T_FLAG.search(segment):
            violations.append(
                "`-T` 없는 전경 `docker run` — heredoc stdin 을 소진할 수 있다. "
                "`docker run --rm -T ... </dev/null` 또는 `-d` 로 교체."
            )

    # Rule 5: 하위 shell 스크립트 호출 (bash X.sh / sh X.sh / ./X.sh) 은
    # heredoc fd 0 을 상속하므로 명시적 stdin 격리가 필요하다.
    if RE_SCRIPT_INVOKE.search(code) and not RE_STDIN_ISOLATED.search(code):
        violations.append(
            "heredoc 내부에서 하위 스크립트 호출 — fd 0 상속으로 잔여 heredoc "
            "라인이 소진될 수 있다. `bash X.sh </dev/null` 로 stdin 을 격리."
        )

    return violations


# ─────────────────────────────────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────────────────────────────────
def iter_target_files() -> list[Path]:
    files: list[Path] = []
    for subdir, pattern in TARGET_GLOBS:
        base = ROOT / subdir
        if not base.exists():
            continue
        files.extend(sorted(base.rglob(pattern)))
    # 본 스크립트 자신은 검사 대상에서 제외 (패턴 예시가 포함되어 있음)
    self_path = Path(__file__).resolve()
    files = [f for f in files if f.resolve() != self_path]
    return files


def scan_file(logical_lines: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """논리 라인 리스트에서 heredoc 블록 내부의 차단 패턴을 수집한다.

    heredoc stack 은 중첩(드문 케이스)을 지원한다. 블록에 진입하면 태그를
    push, 라인이 태그와 정확히 일치하면 pop. stack 이 비어있지 않은 동안은
    ``check_line`` 결과를 수집한다.
    """
    violations: list[tuple[int, str]] = []
    heredoc_stack: list[str] = []

    for line_no, text in logical_lines:
        # 종료 검사 먼저 (heredoc 종료 태그는 라인 전체가 태그여야 함)
        if heredoc_stack:
            stripped = text.strip()
            if stripped == heredoc_stack[-1]:
                heredoc_stack.pop()
                continue

        # heredoc 내부라면 차단 패턴 검사
        if heredoc_stack:
            for msg in check_line(text):
                violations.append((line_no, msg))

        # 시작 검사 — 서브쉘에 먹이는 heredoc 만 관심 대상
        code = strip_comment(text)
        hd_match = RE_HEREDOC_START.search(code)
        if hd_match:
            # 같은 줄에 bash/sh 호출이 있어야 서브쉘 heredoc 으로 판정
            before_hd = code[: hd_match.start()]
            if RE_SUBSHELL_HINT.search(before_hd):
                heredoc_stack.append(hd_match.group("tag"))

    return violations


def main() -> int:
    files = iter_target_files()
    total_violations = 0
    failing_files = 0

    for path in files:
        try:
            lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        except UnicodeDecodeError:
            continue

        logical = join_continuations(lines)
        file_violations = scan_file(logical)

        if file_violations:
            failing_files += 1
            total_violations += len(file_violations)
            rel = path.relative_to(ROOT)
            print(f"\n❌ {rel}")
            for line_no, msg in file_violations:
                print(f"   line {line_no}: {msg}")

    print()
    print("=" * 60)
    if total_violations == 0:
        print(f"✓ CD STDIN GUARD PASSED ({len(files)} files scanned)")
        print("=" * 60)
        return 0
    print(
        f"✗ CD STDIN GUARD FAILED — {total_violations} violation(s) "
        f"in {failing_files} file(s)"
    )
    print("=" * 60)
    print()
    print("근거: CLAUDE.md 'SSH Heredoc 에서 비대화형 원격 명령 작성 규칙'")
    print("배경: docs/operations/daily-report-regression-2026-04-08.md §4.7-§4.8")
    return 1


if __name__ == "__main__":
    sys.exit(main())
