#!/usr/bin/env python3
"""취약점 ignore 목록 만료일 정적 검사기.

정책: CLAUDE.md §9 "화이트리스트 만료일 자동 검증"
배경: 2026-04-23 PR #38 hotfix (CVE-2026-3298) 후속 작업

만료일 검사가 필요한 이유
-------------------------
``.grype.yaml`` 과 ``backend/.pip-audit-ignore`` 는 각 엔트리마다 ``YYYY-MM-DD``
만료일을 의무화하지만, 실제 만료 시점에 아무도 갱신 PR 을 올리지 않으면 **조용히**
CI 의 pip-audit 단계에서 무효화되어 해당 CVE 가 재노출된다. pip-audit 은
``--strict`` 로 실행되어 결국 CI 가 차단되지만, 그 시점에서야 "왜 지금 깨졌지"
를 추적하게 되어 대응이 지연된다. 본 검사기는 **만료일 N 일 전에 미리** 경고
차단하여 갱신 PR 을 선제적으로 유도한다.

검사 규칙
---------
1. ``.grype.yaml`` 과 ``backend/.pip-audit-ignore`` 의 각 엔트리에서 ID 와
   인라인 주석의 ``YYYY-MM-DD`` 만료일을 추출한다.
2. 만료일이 없는 엔트리는 error (의무 규칙 위반).
3. 만료일이 오늘 (UTC) 이전이면 error (이미 만료).
4. 오늘 이후면 통과 (오늘 당일은 "not yet expired" 로 판정).

Exit code: 0 = PASS, 1 = FAIL.

구현 주의
---------
- parity 검사기와 ID 패턴을 공유 (``_ID_PATTERN``) 하지만, 본 검사기는 (ID,
  expiry_date) 쌍을 추출해야 하므로 별도 파서를 둔다.
- ``datetime.date.today()`` 는 local timezone 을 쓰는데, CI 러너는 UTC 이므로
  일관성을 위해 ``datetime.datetime.now(timezone.utc).date()`` 를 쓴다.
- 인라인 주석의 최초 ``YYYY-MM-DD`` 를 만료일로 본다. 주석 안에 여러 날짜가
  있으면 가장 앞에 오는 것이 만료일이라는 규칙 (기존 파일의 관례).
"""

from __future__ import annotations

import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, NamedTuple

REPO_ROOT = Path(__file__).resolve().parent.parent
GRYPE_PATH = REPO_ROOT / ".grype.yaml"
PIP_AUDIT_PATH = REPO_ROOT / "backend" / ".pip-audit-ignore"

# parity 검사기와 동일 — 두 검사기가 같은 domain ID 집합을 대상으로 하므로
# 의도적으로 패턴을 통일. 변경 시 양쪽을 함께 갱신할 것.
_ID_PATTERN = r"(?:CVE-\d{4}-\d{4,7}|GHSA-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4})"

# YAML 블록의 `- vulnerability: <ID>` 라인 (인용 유무 무관).
_GRYPE_LINE = re.compile(
    r"""^\s*-\s*vulnerability:\s*['"]?(?P<id>""" + _ID_PATTERN + r")['\"]?"
)

# .pip-audit-ignore 라인 선두 식별자. 주석은 `#` 이후.
_PIP_AUDIT_LINE = re.compile(r"^\s*(?P<id>" + _ID_PATTERN + r")\b")

# 인라인 주석의 ``YYYY-MM-DD``. `#` 이후에서만 찾는다 (ID 자체에 연도-연도 형식이
# 들어갈 수 있는데 CVE-2026-... 의 4자리는 실제 날짜가 아니므로 주석 경계가
# 필요하다). 첫 매치만 만료일로 사용.
_DATE_PATTERN = re.compile(r"(?P<date>\d{4}-\d{2}-\d{2})")


class Entry(NamedTuple):
    """화이트리스트 한 엔트리.

    Attributes:
        ident: CVE/GHSA 식별자.
        expiry: 만료일. 인라인 주석에서 파싱 실패 시 ``None``.
        source: 표시용 파일 경로 (error 메시지에 사용).
        line_no: 1-based 라인 번호.
    """

    ident: str
    expiry: date | None
    source: str
    line_no: int


def _extract_expiry(line: str) -> date | None:
    """라인의 ``#`` 이후에서 첫 ``YYYY-MM-DD`` 를 파싱.

    Returns:
        파싱된 ``date``. 주석이 없거나 날짜 패턴이 없으면 ``None``.
        날짜가 있지만 실제 달력상 존재하지 않는 날짜 (예: 2026-02-30) 도 ``None``
        으로 처리하여 상위에서 "만료일 없음" 과 동일 error 로 판정한다.
    """
    idx = line.find("#")
    if idx < 0:
        return None
    m = _DATE_PATTERN.search(line, idx)
    if not m:
        return None
    try:
        return datetime.strptime(m.group("date"), "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_file(
    path: Path,
    line_re: re.Pattern[str],
    source_label: str,
) -> list[Entry]:
    """경로를 읽어 각 엔트리를 ``Entry`` 목록으로 반환.

    Args:
        path: 화이트리스트 파일.
        line_re: ID 추출 정규식 (named group ``id``).
        source_label: error 메시지에 표시할 파일 경로 라벨.

    Returns:
        파싱된 엔트리 리스트. 파일이 비어있으면 빈 리스트.
    """
    entries: list[Entry] = []
    for idx, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        m = line_re.match(raw)
        if not m:
            continue
        entries.append(
            Entry(
                ident=m.group("id"),
                expiry=_extract_expiry(raw),
                source=source_label,
                line_no=idx,
            )
        )
    return entries


def parse_grype(path: Path = GRYPE_PATH) -> list[Entry]:
    """``.grype.yaml`` 의 엔트리 파싱."""
    return _parse_file(path, _GRYPE_LINE, ".grype.yaml")


def parse_pip_audit(path: Path = PIP_AUDIT_PATH) -> list[Entry]:
    """``backend/.pip-audit-ignore`` 의 엔트리 파싱."""
    return _parse_file(path, _PIP_AUDIT_LINE, "backend/.pip-audit-ignore")


def check_expiry(entries: Iterable[Entry], today: date) -> list[str]:
    """만료일 검증.

    Args:
        entries: 검사 대상 엔트리 목록.
        today: 기준일 (UTC). 테스트에서 주입 가능하도록 파라미터화.

    Returns:
        정렬된 error 메시지 리스트. 빈 리스트면 모두 유효.
    """
    errors: list[str] = []
    for entry in sorted(entries, key=lambda e: (e.source, e.line_no)):
        if entry.expiry is None:
            errors.append(
                f"{entry.source}:{entry.line_no}: {entry.ident} — 만료일 없음 "
                "(형식 `YYYY-MM-DD` 로 인라인 주석에 명시 필수)"
            )
            continue
        if entry.expiry < today:
            days = (today - entry.expiry).days
            errors.append(
                f"{entry.source}:{entry.line_no}: {entry.ident} — "
                f"만료일 {entry.expiry.isoformat()} 이 {days}일 경과. "
                "upstream fix 또는 갱신 사유와 함께 만료일을 연장하는 PR 필요."
            )
    return errors


def _format_report(errors: Iterable[str]) -> str:
    """CI 로그에 그대로 찍을 수 있는 다중 라인 리포트."""
    lines = ["vuln-ignore 만료일 위반:"]
    for err in errors:
        lines.append(f"  - {err}")
    lines.append(
        "\n근거: 화이트리스트 엔트리는 영구 예외가 금지되며 만료일이 지나면 "
        "CI 의 pip-audit 이 --strict 로 재차단합니다. 본 검사기는 만료 당일에 "
        "CI 가 silent 하게 빨갛게 바뀌기 전에 사전 차단하여 갱신 PR 을 유도합니다."
    )
    return "\n".join(lines)


def main() -> int:
    missing: list[Path] = [p for p in (GRYPE_PATH, PIP_AUDIT_PATH) if not p.exists()]
    if missing:
        for p in missing:
            print(f"ERROR: 필수 화이트리스트 파일 없음: {p}", file=sys.stderr)
        return 1

    today = datetime.now(timezone.utc).date()
    entries = [*parse_grype(), *parse_pip_audit()]
    errors = check_expiry(entries, today)

    if errors:
        print(_format_report(errors), file=sys.stderr)
        return 1

    grype_count = sum(1 for e in entries if e.source == ".grype.yaml")
    pip_count = len(entries) - grype_count
    print(
        f"vuln-ignore expiry OK (grype={grype_count}, pip-audit={pip_count}, "
        f"reference={today.isoformat()} UTC)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
