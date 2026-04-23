#!/usr/bin/env python3
"""취약점 ignore 목록 parity 정적 검사기.

정책: CLAUDE.md §9 "grype.yaml ↔ backend/.pip-audit-ignore parity 정적 검사기"
배경: 2026-04-22 `fix/grype-yaml-glibc-lxml-parity` 회고 (CLAUDE.md §9 lxml TODO)

2026-04-22 회귀 요약
---------------------
`chore/pip-audit-ignore-lxml-xxe` (PR #25) 커밋 당시 lxml GHSA-vfmq-68hx-4jfw
를 ``backend/.pip-audit-ignore`` 에만 등록하고 ``.grype.yaml`` 동일 엔트리를
누락. 결과: PR #25 머지 후 main CI 의 ``anchore/scan-action@v6`` (grype)
스텝이 해당 GHSA 를 다시 high 로 판정하여 차단. 교훈 — **컨테이너
스캐너(grype) 와 파이썬 의존성 스캐너(pip-audit) 는 ignore 목록을 공유하지
않는다.** 새 CVE/GHSA 억제 시 두 파일을 **동일 커밋에서** 병행 업데이트
해야 한다.

검사 규칙
---------
1. ``.grype.yaml`` 의 ``ignore:`` 블록에서 ``- vulnerability: <ID>`` 의 ID
   집합을 추출 (``GRYPE_IDS``).
2. ``backend/.pip-audit-ignore`` 의 각 라인 선두에서 ``CVE-...`` 또는
   ``GHSA-...`` 식별자를 추출 (``PIP_IDS``).
3. ``GRYPE_IDS - PIP_IDS`` 집합(= grype 에만 있는 것) 의 각 ID 가 ``.grype.yaml``
   라인에 ``# grype-only`` 마커를 가지면 예외 허용. 그 외에는 error.
4. 반대 방향 ``PIP_IDS - GRYPE_IDS`` 도 ``# pip-audit-only`` 마커로 예외 허용.
5. 예외 허용은 "의도된 단방향" 전용 (예: 순수 Python 패키지는 grype 가 감지
   하지 못하고, OS 패키지는 pip-audit 이 감지하지 못한다).

Exit code: 0 = PASS, 1 = FAIL (상호 배타적 차집합에 marker 없는 ID 존재).

구현 주의
---------
- PyYAML 을 직접 import 하지 않는다. ``backend/requirements.txt`` / ``backend/
  requirements-dev.txt`` 양쪽 모두에 PyYAML 이 없고, Doc Sync 워크플로는
  ``actions/setup-python`` 만 거친 클린 인터프리터로 실행된다. 대신 정규식
  기반 라인 파서로 ``- vulnerability: <ID>`` 와 ``- vulnerability: "<ID>"``
  인용 변형을 모두 수용한다.
- 라인 기반 파서는 마커 주석 판정과 ID 추출을 **같은 패스**로 수행해야
  하므로 결과를 ``dict[str, bool]`` (ID → has_marker) 로 반환한다.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
GRYPE_PATH = REPO_ROOT / ".grype.yaml"
PIP_AUDIT_PATH = REPO_ROOT / "backend" / ".pip-audit-ignore"

# ID 형식: CVE-YYYY-NNNN+ (4자리 이상 숫자) 또는 GHSA-xxxx-xxxx-xxxx (4자
# 블록 3개). PR #25 회귀 방어선과 동일한 관용 범위.
_ID_PATTERN = r"(?:CVE-\d{4}-\d{4,7}|GHSA-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4})"

# .grype.yaml 라인에서 ID 추출: `- vulnerability: CVE-...` / `- vulnerability: "GHSA-..."`.
# 들여쓰기 무관. 마커 판정은 전체 라인을 별도로 검사.
_GRYPE_LINE = re.compile(
    r"""^\s*-\s*vulnerability:\s*['"]?(?P<id>""" + _ID_PATTERN + r")['\"]?"
)

# .pip-audit-ignore 라인 선두 식별자 (주석은 `#` 이후). 선두 공백 허용.
_PIP_AUDIT_LINE = re.compile(r"^\s*(?P<id>" + _ID_PATTERN + r")\b")

# 마커 주석 정규식. 단어 경계 기반 — 인라인 주석(`# <expiry> grype-only`)
# 어느 위치에 있어도 인정. `_GRYPE_LINE` / `_PIP_AUDIT_LINE` 이 라인을
# 이미 벤더 엔트리로 제약하므로 본 토큰이 ID/키 안에 섞일 여지는 없다.
_GRYPE_ONLY_MARKER = re.compile(r"\bgrype-only\b")
_PIP_AUDIT_ONLY_MARKER = re.compile(r"\bpip-audit-only\b")


def _parse_file(
    path: Path,
    line_re: re.Pattern[str],
    marker_re: re.Pattern[str],
) -> dict[str, bool]:
    """경로에서 ID 를 추출하고 라인에 붙은 single-direction 마커 여부를 기록.

    Args:
        path: 화이트리스트 파일.
        line_re: ID 추출 정규식 (named group ``id``).
        marker_re: 단방향 허용 마커 정규식.

    Returns:
        {ID: has_marker} dict. 파일이 없으면 빈 dict.

    Raises:
        FileNotFoundError: 호출부가 사전 존재 확인을 하지 않고 들어온 경우.
    """
    result: dict[str, bool] = {}
    # encoding 명시 — macOS + linux 사이 default 가 다를 수 있어 항상 utf-8.
    for raw in path.read_text(encoding="utf-8").splitlines():
        m = line_re.match(raw)
        if not m:
            continue
        ident = m.group("id")
        has_marker = bool(marker_re.search(raw))
        # 같은 ID 가 여러 번 등장할 일은 없지만 (중복 등록은 별도 lint 범위),
        # 혹시 중복이면 마커가 하나라도 붙은 쪽을 우선.
        result[ident] = result.get(ident, False) or has_marker
    return result


def parse_grype(path: Path = GRYPE_PATH) -> dict[str, bool]:
    """``.grype.yaml`` ignore 항목 파싱."""
    return _parse_file(path, _GRYPE_LINE, _GRYPE_ONLY_MARKER)


def parse_pip_audit(path: Path = PIP_AUDIT_PATH) -> dict[str, bool]:
    """``backend/.pip-audit-ignore`` 항목 파싱."""
    return _parse_file(path, _PIP_AUDIT_LINE, _PIP_AUDIT_ONLY_MARKER)


def check_parity(
    grype: dict[str, bool],
    pip_audit: dict[str, bool],
) -> list[str]:
    """상호 배타적 차집합을 판정하여 error 메시지 리스트를 반환.

    규칙:
      - grype 에만 있고 ``# grype-only`` 마커 없는 ID → error.
      - pip-audit 에만 있고 ``# pip-audit-only`` 마커 없는 ID → error.
      - 양쪽 모두 있거나 해당 방향 마커가 있으면 통과.

    Returns:
        정렬된 error 메시지 리스트. 빈 리스트면 parity 통과.
    """
    errors: list[str] = []

    grype_only = set(grype) - set(pip_audit)
    for ident in sorted(grype_only):
        if not grype[ident]:
            errors.append(
                f"{ident}: .grype.yaml 에만 존재. backend/.pip-audit-ignore 에 "
                "추가하거나 grype 라인에 `# grype-only` 마커를 붙이세요."
            )

    pip_only = set(pip_audit) - set(grype)
    for ident in sorted(pip_only):
        if not pip_audit[ident]:
            errors.append(
                f"{ident}: backend/.pip-audit-ignore 에만 존재. .grype.yaml 에 "
                "추가하거나 pip-audit 라인에 `# pip-audit-only` 마커를 "
                "붙이세요."
            )

    return errors


def _format_report(errors: Iterable[str]) -> str:
    """CI 로그에 그대로 찍을 수 있는 다중 라인 리포트."""
    lines = ["vuln-ignore parity 위반:"]
    for err in errors:
        lines.append(f"  - {err}")
    lines.append(
        "\n근거: 2026-04-22 lxml GHSA-vfmq-68hx-4jfw silent miss (CLAUDE.md §9). "
        "grype 와 pip-audit 는 ignore 목록을 공유하지 않으므로 신규 CVE/GHSA "
        "억제는 두 파일에 동시에 반영해야 합니다."
    )
    return "\n".join(lines)


def main() -> int:
    missing: list[Path] = [p for p in (GRYPE_PATH, PIP_AUDIT_PATH) if not p.exists()]
    if missing:
        for p in missing:
            print(f"ERROR: 필수 화이트리스트 파일 없음: {p}", file=sys.stderr)
        return 1

    grype = parse_grype()
    pip_audit = parse_pip_audit()
    errors = check_parity(grype, pip_audit)

    if errors:
        print(_format_report(errors), file=sys.stderr)
        return 1

    print(
        f"vuln-ignore parity OK (grype={len(grype)}, pip-audit={len(pip_audit)}, "
        f"shared={len(set(grype) & set(pip_audit))})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
