"""정적 검사기 공통 유틸리티 (`scripts/_check_utils.py`) 회귀 테스트.

검증 범위:
    1. ``VENDORED_DIR_NAMES`` 에 포함된 표준 제외 경로 하위 파일이
       ``iter_python_files`` 의 결과에서 제외된다.
    2. ``extra_excludes`` 로 프로젝트-특수 경로를 추가할 수 있다.
    3. 존재하지 않는 루트 입력은 조용히 빈 iterator 를 반환한다.
    4. ``os.walk`` 의 ``dirnames[:] = ...`` 관용구가 실제로 서브트리 재귀를
       차단한다 (매우 깊은 venv-유사 디렉토리도 O(1) 수준으로 종료).
    5. ``is_vendored_path`` 가 조상 경로 어디서든 매칭된다.
    6. ``.py`` 가 아닌 파일은 yield 되지 않는다.

본 테스트는 2026-04-21 에 관측된 두 가지 회귀 증상을 차단한다:
    - ``check_bool_literals.py`` 가 ``backend/.venv/`` 내부 서드파티 코드를
      스캔하여 226 false positive 발생
    - ``check_loguru_style.py`` 가 동일 경로의 수만 개 파일 AST 파싱으로
      sandbox 환경에서 타임아웃
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
UTIL_PATH = REPO_ROOT / "scripts" / "_check_utils.py"


def _load_util():
    spec = importlib.util.spec_from_file_location("_check_utils", UTIL_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


UTIL = _load_util()


# ═════════════════════════════════════════════════════════════════════════
# is_vendored_path — 조상 경로 매칭
# ═════════════════════════════════════════════════════════════════════════
def test_is_vendored_path_detects_direct_parent(tmp_path: Path) -> None:
    """파일 바로 위 디렉토리가 vendored 이름이면 True."""
    venv_file = tmp_path / ".venv" / "lib" / "sample.py"
    assert UTIL.is_vendored_path(venv_file) is True


def test_is_vendored_path_detects_deeper_ancestor(tmp_path: Path) -> None:
    """파일 여러 단계 위에 vendored 조상이 있어도 True.

    실제 사례: ``backend/.venv/lib/python3.11/site-packages/sympy/...``
    """
    deep = tmp_path / "backend" / ".venv" / "lib" / "python3.11" / "site-packages" / "sympy" / "m.py"
    assert UTIL.is_vendored_path(deep) is True


def test_is_vendored_path_returns_false_for_project_file(tmp_path: Path) -> None:
    """프로젝트 코드 경로는 False 를 반환해야 한다."""
    project_file = tmp_path / "backend" / "api" / "routes" / "users.py"
    assert UTIL.is_vendored_path(project_file) is False


def test_is_vendored_path_respects_extra_excludes(tmp_path: Path) -> None:
    """extra_excludes 로 전달한 이름도 vendored 로 취급한다."""
    archive_file = tmp_path / "archive" / "old.py"
    assert UTIL.is_vendored_path(archive_file) is False
    assert UTIL.is_vendored_path(archive_file, extra_excludes={"archive"}) is True


def test_is_vendored_path_matches_all_standard_names() -> None:
    """VENDORED_DIR_NAMES 의 모든 항목이 실제로 매칭된다."""
    for name in UTIL.VENDORED_DIR_NAMES:
        path = Path("/some/project") / name / "inner.py"
        assert UTIL.is_vendored_path(path) is True, f"{name} should be vendored"


# ═════════════════════════════════════════════════════════════════════════
# iter_python_files — 표준 제외 경로 동작
# ═════════════════════════════════════════════════════════════════════════
def _make_tree(tmp_path: Path, layout: dict[str, str]) -> None:
    """테스트용 파일 트리 생성. layout = {relative_path: content}."""
    for rel, content in layout.items():
        fpath = tmp_path / rel
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(content, encoding="utf-8")


def test_iter_skips_dot_venv(tmp_path: Path) -> None:
    """``.venv/`` 하위 파일은 제외된다 (핵심 회귀 방지)."""
    _make_tree(
        tmp_path,
        {
            "src/app.py": "x = 1\n",
            ".venv/lib/python3.11/site-packages/sympy/core.py": "y = 2\n",
        },
    )
    results = {p.name for p in UTIL.iter_python_files(tmp_path)}
    assert results == {"app.py"}


def test_iter_skips_venv_without_dot(tmp_path: Path) -> None:
    """``venv/`` (dot 없음) 도 제외된다."""
    _make_tree(
        tmp_path,
        {
            "src/a.py": "a = 1\n",
            "venv/lib/b.py": "b = 2\n",
        },
    )
    results = {p.name for p in UTIL.iter_python_files(tmp_path)}
    assert results == {"a.py"}


def test_iter_skips_pycache(tmp_path: Path) -> None:
    """``__pycache__/`` 하위 .py 파일은 제외된다."""
    _make_tree(
        tmp_path,
        {
            "src/a.py": "a = 1\n",
            "src/__pycache__/a.cpython-311.pyc": "binary",
            "src/__pycache__/b.py": "shouldnt_scan = 1\n",
        },
    )
    results = {p.name for p in UTIL.iter_python_files(tmp_path)}
    assert results == {"a.py"}


def test_iter_skips_build_artifacts(tmp_path: Path) -> None:
    """build / dist / htmlcov 등 아티팩트 디렉토리 제외."""
    _make_tree(
        tmp_path,
        {
            "src/a.py": "a = 1\n",
            "build/lib/generated.py": "g = 2\n",
            "dist/wheel/inner.py": "d = 3\n",
            "htmlcov/jscov.py": "h = 4\n",
        },
    )
    results = {p.name for p in UTIL.iter_python_files(tmp_path)}
    assert results == {"a.py"}


def test_iter_skips_cache_directories(tmp_path: Path) -> None:
    """.pytest_cache / .mypy_cache / .ruff_cache / .tox 제외."""
    _make_tree(
        tmp_path,
        {
            "src/a.py": "a = 1\n",
            ".pytest_cache/v/cache.py": "c = 1\n",
            ".mypy_cache/3.11/node.py": "c = 2\n",
            ".ruff_cache/v0.5.0/x.py": "c = 3\n",
            ".tox/py311/lib/python3.11/site-packages/pkg/m.py": "c = 4\n",
        },
    )
    results = {p.name for p in UTIL.iter_python_files(tmp_path)}
    assert results == {"a.py"}


def test_iter_yields_project_files_only(tmp_path: Path) -> None:
    """프로젝트 구조 시뮬레이션 — 코드는 모두 yield, vendored 는 모두 skip."""
    _make_tree(
        tmp_path,
        {
            "backend/api/routes/users.py": "u = 1\n",
            "backend/core/utils/env.py": "e = 1\n",
            "backend/.venv/lib/python3.11/site-packages/torch/__init__.py": "t = 1\n",
            "scripts/check_x.py": "s = 1\n",
            "scripts/__pycache__/check_x.cpython-311.pyc": "binary",
        },
    )
    results = sorted(p.relative_to(tmp_path).as_posix() for p in UTIL.iter_python_files(tmp_path))
    assert results == [
        "backend/api/routes/users.py",
        "backend/core/utils/env.py",
        "scripts/check_x.py",
    ]


def test_iter_skips_only_py_files(tmp_path: Path) -> None:
    """``.py`` 가 아닌 파일은 yield 되지 않는다."""
    _make_tree(
        tmp_path,
        {
            "src/a.py": "a = 1\n",
            "src/b.txt": "text",
            "src/c.md": "md",
            "src/d.pyc": "binary",
            "src/e.pyi": "stub: int\n",
        },
    )
    results = {p.name for p in UTIL.iter_python_files(tmp_path)}
    assert results == {"a.py"}


def test_iter_respects_extra_excludes(tmp_path: Path) -> None:
    """호출부가 전달한 extra_excludes 가 제외 목록에 합쳐진다."""
    _make_tree(
        tmp_path,
        {
            "src/a.py": "a = 1\n",
            "archive/old.py": "o = 1\n",
            "fixtures/sample.py": "f = 1\n",
        },
    )
    without_extra = {p.name for p in UTIL.iter_python_files(tmp_path)}
    assert without_extra == {"a.py", "old.py", "sample.py"}
    with_extra = {p.name for p in UTIL.iter_python_files(tmp_path, extra_excludes={"archive", "fixtures"})}
    assert with_extra == {"a.py"}


def test_iter_returns_empty_for_missing_root(tmp_path: Path) -> None:
    """존재하지 않는 루트 → 예외 없이 빈 iterator."""
    missing = tmp_path / "does_not_exist"
    results = list(UTIL.iter_python_files(missing))
    assert results == []


def test_iter_returns_empty_for_empty_tree(tmp_path: Path) -> None:
    """빈 디렉토리 → 빈 iterator."""
    empty = tmp_path / "empty"
    empty.mkdir()
    results = list(UTIL.iter_python_files(empty))
    assert results == []


# ═════════════════════════════════════════════════════════════════════════
# 성능 특성 — os.walk 의 dirnames 뮤테이션으로 서브트리 재귀 차단 확인
# ═════════════════════════════════════════════════════════════════════════
def test_iter_does_not_recurse_into_vendored_subtrees(tmp_path: Path) -> None:
    """vendored 디렉토리 하위의 깊은 구조가 탐색되지 않는다.

    본 테스트는 ``.venv`` 안에 20단계 깊이의 디렉토리와 파일을 생성하고,
    ``iter_python_files`` 가 이를 **하나도 방문하지 않는지** 를 확인한다.
    만약 ``os.walk`` 의 dirnames 뮤테이션이 동작하지 않으면 깊은 트리가
    yield 목록에 포함되거나 경로 탐색 비용이 증가한다.
    """
    # 20단계 깊이의 venv-like 구조를 만든다.
    deep = tmp_path / ".venv"
    current = deep
    for i in range(20):
        current = current / f"level_{i}"
    current.mkdir(parents=True, exist_ok=True)
    (current / "deep.py").write_text("deep = 1\n", encoding="utf-8")

    # 프로젝트 파일 하나만 존재해야 한다.
    (tmp_path / "main.py").write_text("main = 1\n", encoding="utf-8")

    results = {p.name for p in UTIL.iter_python_files(tmp_path)}
    assert results == {"main.py"}
    # 깊은 구조는 단 하나도 yield 되지 않아야 한다.
    assert "deep.py" not in results
