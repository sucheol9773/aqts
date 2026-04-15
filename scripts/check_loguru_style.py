#!/usr/bin/env python3
"""loguru 스타일 정적 검사기.

정책
----
``config.logging`` 에서 export 되는 ``logger`` 는 loguru 객체이며,
**stdlib logging 의 ``%`` posarg 포맷을 해석하지 않는다**. 즉 ::

    logger.info("positions=%d", n)      # ← 잘못된 예 (literal 로 남음)
    logger.error("failure: %s", exc)    # ← 잘못된 예

위 호출은 런타임 에러를 발생시키지 않고 **조용히 메시지를 literal 로 기록**
하기 때문에, 관측 가능성 결손 (silent miss) 이 된다. 회고 참조:
``docs/operations/phase1-demo-verification-2026-04-11.md §10.15``.

올바른 사용은 loguru 의 ``{}`` 포맷 또는 f-string 이다 ::

    logger.info(f"positions={n}")
    logger.info("positions={}", n)

stdlib ``logging`` 을 import 하고 ``logging.getLogger(...)`` 로 얻은 logger
는 ``%`` posarg 를 지원하므로 검사에서 제외한다.

검사 범위
---------
``backend/**/*.py`` 에서 ``from config.logging import logger`` 또는
``from loguru import logger`` 를 import 한 파일을 대상으로 하고,
그 외 파일은 제외한다 (stdlib logging 사용 가능성 때문).

Exit code: 0 = PASS, 1 = FAIL.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"

# loguru logger 를 import 하는 것으로 간주되는 패턴.
# stdlib logging 은 이 둘 중 어느 것도 매치되지 않음 (분리 보장).
LOGURU_IMPORT_PATTERNS = (
    re.compile(r"^from\s+config\.logging\s+import\s+.*\blogger\b", re.MULTILINE),
    re.compile(r"^from\s+loguru\s+import\s+.*\blogger\b", re.MULTILINE),
)

# logger.<level>("...%d...", arg) / logger.<level>("...%s...", arg) 형태.
# f-string (f"...{var}...") 과 loguru 포맷 ("...{}...") 은 매치하지 않음.
# 단일/이중 따옴표 모두 잡고, 끝 따옴표 이후 comma 가 있어야 posarg 로 판정.
BAD_POSARG_PATTERN = re.compile(
    r"logger\s*\.\s*(?:info|warning|error|debug|critical|trace|success|exception)"
    r"\s*\(\s*[^,()]*?%[ds][^,()]*?[\"'],\s*[^)]",
    re.DOTALL,
)


def _uses_loguru(source: str) -> bool:
    """파일이 loguru logger 를 import 하는지 판정."""
    return any(pat.search(source) for pat in LOGURU_IMPORT_PATTERNS)


def scan() -> list[tuple[Path, int, str]]:
    """백엔드 전체에서 loguru posarg 오용 위치를 수집."""
    violations: list[tuple[Path, int, str]] = []
    for path in sorted(BACKEND.rglob("*.py")):
        # tests 디렉토리도 포함한다 — 테스트에서도 동일한 실수 재발 가능.
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not _uses_loguru(source):
            continue
        for match in BAD_POSARG_PATTERN.finditer(source):
            # 매치된 구간의 시작 줄 번호 계산.
            line_no = source.count("\n", 0, match.start()) + 1
            snippet = match.group(0).strip().splitlines()[0][:120]
            violations.append((path.relative_to(ROOT), line_no, snippet))
    return violations


def main() -> int:
    violations = scan()
    if not violations:
        print("✓ LOGURU STYLE CHECK PASSED — no '%d'/'%s' posarg usage with loguru logger")
        return 0
    print("✗ LOGURU STYLE CHECK FAILED")
    print(
        f"  Found {len(violations)} loguru call(s) using stdlib-style '%'"
        " posarg format."
    )
    print(
        "  loguru does NOT interpret '%d'/'%s' posargs — use f-string or '{}' format."
    )
    print(
        "  Reference: docs/operations/phase1-demo-verification-2026-04-11.md §10.15"
    )
    for path, line_no, snippet in violations:
        print(f"    {path}:{line_no}: {snippet}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
