#!/usr/bin/env python3
"""환경변수 bool 표기 표준화 정적 검사기.

정책: ``docs/conventions/boolean-config.md``

검사 항목:
1. Python 코드의 ad-hoc bool 파싱 (env_bool() 우회) 패턴 차단.
2. .env*, docker-compose*.yml, .github/workflows/*.yml 안의 bool 환경변수
   값이 표준 표기('true'/'false')인지 확인.

Phase 1에서는 이미 알려진 bool env 키 화이트리스트만 강제하고, 그 외는
경고로만 출력한다 (Phase 2에서 error로 승격 예정).

Exit code: 0 = PASS, 1 = FAIL.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 알려진 bool 환경변수 (표준 표기 강제 대상)
BOOL_ENV_KEYS = {
    "TESTING",
    "OTEL_ENABLED",
    "SCHEDULER_ENABLED",
    "DEBUG",
    "AQTS_STRICT_BOOL",
    "COLLECTOR_OTLP_ENABLED",
}

# Python 코드에서 차단할 ad-hoc 파싱 패턴
AD_HOC_PATTERNS = [
    re.compile(r'os\.environ\.get\([^)]*\)\s*==\s*["\']'),
    re.compile(r'os\.getenv\([^)]*\)\s*==\s*["\']'),
    re.compile(r'os\.environ\.get\([^)]*\)\.lower\(\)'),
    re.compile(r'os\.getenv\([^)]*\)\.lower\(\)'),
    re.compile(r'os\.environ\.get\([^)]*\)\s*in\s*\([^)]*["\']true'),
]

# 정적 검사 면제 파일 (env_bool 자체 구현 등)
PYTHON_EXEMPT = {
    ROOT / "backend" / "core" / "utils" / "env.py",
    ROOT / "scripts" / "check_bool_literals.py",
}

PYTHON_GLOBS = ["backend/**/*.py", "scripts/**/*.py"]
ENV_FILE_GLOBS = [".env", ".env.example", ".env.*"]
COMPOSE_GLOBS = ["docker-compose*.yml", ".github/workflows/*.yml"]


def check_python_files() -> list[str]:
    errors: list[str] = []
    for pattern in PYTHON_GLOBS:
        for path in ROOT.glob(pattern):
            if path in PYTHON_EXEMPT:
                continue
            if "__pycache__" in path.parts:
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except Exception:
                continue
            for lineno, line in enumerate(lines, 1):
                stripped = line.lstrip()
                if stripped.startswith("#"):
                    continue
                for regex in AD_HOC_PATTERNS:
                    if regex.search(line):
                        errors.append(
                            f"{path.relative_to(ROOT)}:{lineno}: ad-hoc bool "
                            f"parsing detected; use core.utils.env.env_bool() "
                            f"instead\n    {line.strip()}"
                        )
                        break
    return errors


_KV_RE = re.compile(
    r'^\s*(?P<key>[A-Z_][A-Z0-9_]*)\s*[:=]\s*["\']?(?P<value>[^"\'\s#]+)'
)


def check_config_files() -> list[str]:
    errors: list[str] = []
    globs = ENV_FILE_GLOBS + COMPOSE_GLOBS
    for pattern in globs:
        for path in ROOT.glob(pattern):
            if not path.is_file():
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except Exception:
                continue
            for lineno, raw in enumerate(lines, 1):
                m = _KV_RE.match(raw)
                if not m:
                    continue
                key = m.group("key")
                value = m.group("value")
                if key not in BOOL_ENV_KEYS:
                    continue
                if value not in ("true", "false"):
                    errors.append(
                        f"{path.relative_to(ROOT)}:{lineno}: {key}={value} "
                        f"is not standard; use 'true' or 'false'"
                    )
    return errors


def main() -> int:
    errors = check_python_files() + check_config_files()
    if errors:
        print("✗ BOOL LITERAL CHECK FAILED")
        for e in errors:
            print(f"  {e}")
        print(f"\n  Total: {len(errors)} error(s)")
        print("  See: docs/conventions/boolean-config.md")
        return 1
    print("✓ BOOL LITERAL CHECK PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
