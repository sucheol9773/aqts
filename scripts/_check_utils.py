"""정적 검사기 공통 유틸리티.

검사 스크립트들이 파일 시스템을 스캔할 때 Python 소스가 아닌 경로
(venv, build artifacts, cache) 를 일관되게 제외하기 위한 단일 진입점.

배경
-----
각 검사기가 개별적으로 경로 제외 로직을 구현하면 한 곳만 놓쳐도 로컬
venv/build artifact 안의 서드파티 코드가 스캔되어 false positive 또는
성능 저하를 유발한다. 2026-04-21 관측:

    - ``check_bool_literals.py``: ``backend/.venv/`` 스캔으로 226 false
      positives (sympy/torch/colab-utils 등 서드파티의 ``os.environ.get(...)
      == "true"`` 패턴이 프로젝트 규칙 위반으로 오탐)
    - ``check_loguru_style.py``: ``backend/.venv/`` 하위 AST 파싱 수만 개
      파일 누적으로 sandbox 환경에서 15s 타임아웃 초과

설계
-----
1. 표준 제외 경로는 ``VENDORED_DIR_NAMES`` SSOT 로 관리한다.
2. 호출부에서 프로젝트-특수 경로를 추가할 수 있도록 ``extra_excludes``
   파라미터를 노출한다.
3. 내부 구현은 ``os.walk`` 로 트리를 순회하며 vendored 디렉토리를 탐색
   대상에서 **사전 제거** 한다. ``rglob`` 은 모든 파일을 열거한 뒤 필터링
   하므로 대용량 venv 안으로 내려가는 비용을 지불하게 된다.
4. 본 파일 자체는 검사기가 아니므로 ``.github/workflows/doc-sync-check.yml``
   에 등록하지 않는다.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Iterator

# Vendored / 자동생성 디렉토리 SSOT.
#
# .venv / venv     : Python 가상환경 (로컬 개발자 머신 + sandbox 마운트)
# __pycache__      : Python bytecode cache
# .tox             : tox 가상환경
# build / dist     : setuptools / wheel 빌드 아티팩트
# .pytest_cache    : pytest cache
# htmlcov          : coverage HTML 리포트
# .mypy_cache      : mypy type-check cache
# .ruff_cache      : ruff lint cache
# node_modules     : Node.js 의존성 (future frontend bridge 대비)
# site-packages    : 설치된 패키지 루트 (일반적으로 venv 하위지만 명시)
VENDORED_DIR_NAMES: frozenset[str] = frozenset(
    {
        ".venv",
        "venv",
        "__pycache__",
        ".tox",
        "build",
        "dist",
        ".pytest_cache",
        "htmlcov",
        ".mypy_cache",
        ".ruff_cache",
        "node_modules",
        "site-packages",
    }
)


def is_vendored_path(path: Path, extra_excludes: Iterable[str] = ()) -> bool:
    """파일 경로가 vendored / 자동생성 디렉토리 하위인지 판정.

    ``path.parts`` 를 분해하여 모든 조상 디렉토리 이름을
    ``VENDORED_DIR_NAMES | extra_excludes`` 와 비교한다. 하나라도 일치하면
    ``True``.

    Args:
        path: 검사 대상 파일 경로.
        extra_excludes: 표준 제외 목록에 더할 디렉토리 이름.

    Returns:
        True 이면 해당 파일은 검사 대상에서 제외되어야 한다.
    """
    excludes = set(VENDORED_DIR_NAMES) | set(extra_excludes)
    return any(part in excludes for part in path.parts)


def iter_python_files(
    root: Path,
    *,
    extra_excludes: Iterable[str] = (),
) -> Iterator[Path]:
    """``root`` 하위의 ``.py`` 파일을 순회하되 vendored 경로는 제외.

    내부 구현은 ``os.walk`` 로 디렉토리 트리를 순회하면서 vendored 디렉토리
    자체를 ``dirnames`` 리스트에서 제거한다. ``os.walk`` 는 ``dirnames`` 를
    mutate 하면 해당 서브트리를 더 이상 재귀하지 않으므로, 대용량
    ``venv`` / ``node_modules`` / ``site-packages`` 안으로 내려가는 비용을
    원천 차단한다.

    Args:
        root: 검색 루트 디렉토리. 존재하지 않으면 빈 iterator.
        extra_excludes: 표준 제외 목록에 추가할 디렉토리 이름.

    Yields:
        vendored 경로에 포함되지 않는 ``.py`` 파일 경로. 순서는 ``os.walk``
        구현에 의존하며 보장되지 않는다. 필요시 호출부에서 ``sorted()``
        로 고정 순서를 확보한다.
    """
    if not root.exists():
        return
    excludes = set(VENDORED_DIR_NAMES) | set(extra_excludes)
    for dirpath, dirnames, filenames in os.walk(root):
        # dirnames[:] 슬라이스 대입으로 os.walk 의 내부 상태를 mutate 한다.
        # 일반 재할당 (dirnames = ...) 은 지역 변수만 바꾸고 재귀에 영향을
        # 주지 못한다. 이 관용구가 os.walk 의 표준 제외 패턴이다.
        dirnames[:] = [d for d in dirnames if d not in excludes]
        for fname in filenames:
            if fname.endswith(".py"):
                yield Path(dirpath) / fname
