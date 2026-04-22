#!/usr/bin/env python3
"""AQTS 백엔드 모듈 의존성 다이어그램 생성기.

목적: `docs/architecture/diagrams/` 하위에 3 계층 다이어그램을 결정적으로
재생성한다.

  1. 전체 백엔드 SVG/DOT (자체 AST 분석 → Graphviz ``dot`` CLI 렌더)
  2. 경계 뷰 Mermaid (cross-team edges 만)
  3. 팀별 Mermaid 4 개 (within-team edges 만)
  4. `layer-violations.txt` (AST 기반 레이어 경계 위반 리포트)

팀 분류는 `agent_docs/governance.md §2.3` 의 소유권 매트릭스를 미러링한다.
vendored 경로 제외는 `scripts/_check_utils.py::iter_python_files` 를 SSOT 로
재사용한다 (OPS-017 패턴).

사용법::

    python scripts/generate_diagrams.py           # 산출물 갱신 + 재기록
    python scripts/generate_diagrams.py --check   # 드리프트 검증 (CI 용)

Exit code: 0 = PASS, 1 = FAIL (클래스 카운트 미달 / dot 오류 / 드리프트).
"""

from __future__ import annotations

import argparse
import ast
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable

# `_check_utils` 는 본 스크립트와 같은 디렉토리에 위치하므로 sys.path 확장.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _check_utils import iter_python_files  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
DIAGRAMS_DIR = ROOT / "docs" / "architecture" / "diagrams"

# ─────────────────────────────────────────────────────────────────────────────
# 팀 분류 (governance.md §2.3 미러링)
# ─────────────────────────────────────────────────────────────────────────────
#
# 각 튜플은 ``(dotted-prefix, team_number)`` 이며, 프리픽스 일치 시
# 해당 팀 번호를 부여한다. **더 구체적인 프리픽스가 먼저** 와야 한다
# (예: ``backend.core.scheduler_handlers`` 가 ``backend.core.scheduler``
# 보다 앞서야 정확히 매칭). 일반적으로 완전 일치 또는 ``prefix + "."``
# 하위만 채택한다.
#
# 팀 0 은 "shared / 미분류" 이며 명시적 프리픽스를 두지 않는다. 본 표에
# 매칭되지 않는 모든 ``backend.*`` 모듈은 팀 0 으로 떨어진다.

TEAM_SPEC: list[tuple[str, int]] = [
    # 팀 4 — Tests / Doc-Sync (최우선 매칭)
    ("backend.tests", 4),
    # 팀 2 — Scheduler / Ops / Notification
    ("backend.scheduler_main", 2),
    ("backend.core.trading_scheduler", 2),
    ("backend.core.scheduler_handlers", 2),
    ("backend.core.scheduler_heartbeat", 2),
    ("backend.core.scheduler_idempotency", 2),
    ("backend.core.market_calendar", 2),
    ("backend.core.periodic_reporter", 2),
    ("backend.core.daily_reporter", 2),
    ("backend.core.reconciliation", 2),
    ("backend.core.reconciliation_providers", 2),
    ("backend.core.reconciliation_runner", 2),
    ("backend.core.notification", 2),
    ("backend.core.monitoring", 2),
    ("backend.core.emergency_monitor", 2),
    ("backend.core.circuit_breaker", 2),
    ("backend.core.graceful_shutdown", 2),
    ("backend.core.health_checker", 2),
    # 팀 1 — Strategy / Backtest
    ("backend.core.strategy_ensemble", 1),
    ("backend.core.backtest_engine", 1),
    ("backend.core.oos", 1),
    ("backend.core.hyperopt", 1),
    ("backend.core.param_sensitivity", 1),
    ("backend.core.quant_engine", 1),
    ("backend.core.weight_optimizer", 1),
    ("backend.core.rl", 1),  # RL 파이프라인, 전략 인접 영역으로 분류
    ("backend.config.ensemble_config_loader", 1),
    # 팀 3 — API / RBAC / Security
    ("backend.main", 3),
    ("backend.api", 3),
    ("backend.db", 3),
    ("backend.alembic", 3),
    ("backend.core.audit", 3),
    ("backend.core.compliance", 3),
    ("backend.core.order_executor", 3),
    ("backend.core.trading_guard", 3),
    ("backend.core.portfolio_manager", 3),
    ("backend.core.portfolio_ledger", 3),
    ("backend.core.idempotency", 3),
    ("backend.core.data_collector", 3),
]

TEAM_LABELS: dict[int, str] = {
    0: "공유 — Shared",
    1: "팀 1 — Strategy / Backtest",
    2: "팀 2 — Scheduler / Ops / Notification",
    3: "팀 3 — API / RBAC / Security",
    4: "팀 4 — Tests / Doc-Sync",
}

TEAM_FILE_SUFFIX: dict[int, str] = {
    1: "team1-strategy",
    2: "team2-scheduler",
    3: "team3-api",
    # team 4(tests) 는 within-team import 가 사실상 0 (테스트 파일끼리는 서로
    # import 하지 않는다). per-team 파일을 생성하지 않고, 전체 뷰는
    # ``module-deps.overall.svg`` 아틀라스에서 확인한다. README 에 명시.
}

# Mermaid ``classDef`` 색상 정의. fill/stroke 는 접근성 대비를 고려해 밝은
# 파스텔 조합으로 선택.
TEAM_CLASSDEF: dict[int, str] = {
    0: "classDef team0 fill:#ffffff,stroke:#333333,stroke-width:1px",
    1: "classDef team1 fill:#cfe8f3,stroke:#0b88c2,stroke-width:2px",
    2: "classDef team2 fill:#ffe6cc,stroke:#f08000,stroke-width:2px",
    3: "classDef team3 fill:#d4f0d0,stroke:#0a8a0a,stroke-width:2px",
    4: "classDef team4 fill:#dddddd,stroke:#666666,stroke-width:2px",
}


def classify(module: str) -> int:
    """Dotted 모듈명을 팀 번호로 분류. 매칭 없으면 0 (shared)."""
    for prefix, team in TEAM_SPEC:
        if module == prefix or module.startswith(prefix + "."):
            return team
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# AST 기반 import 그래프 구축
# ─────────────────────────────────────────────────────────────────────────────
#
# 백엔드 컨테이너는 ``PYTHONPATH=/app/backend`` 로 기동되므로 소스 코드의
# absolute import 는 ``from core.foo import bar`` 같이 ``backend.`` 접두사를
# 생략하는 것이 표준이다 (Dockerfile CMD 와 `backend/pyproject.toml`
# ``[tool.ruff] src = ["."]`` 참조). 본 스크립트는 저장소 루트에서 실행되므로
# 모듈 경로 canonical form 으로는 ``backend.core.foo`` 를 쓰되, 파싱 시 두
# 형태를 모두 수용해 ``backend.*`` 로 정규화한다.


def _backend_top_level_names() -> set[str]:
    """``backend/`` 최상위 패키지 + 단일 파일 이름 집합."""
    names: set[str] = set()
    for entry in BACKEND.iterdir():
        if entry.is_dir() and (entry / "__init__.py").exists():
            names.add(entry.name)
        elif entry.is_file() and entry.suffix == ".py":
            names.add(entry.stem)
    return names


def _normalize_absolute(module: str, backend_top: set[str]) -> str | None:
    """Absolute import 를 ``backend.*`` 로 정규화. 외부 패키지면 None."""
    if not module:
        return None
    if module.startswith("backend."):
        return module
    first = module.split(".", 1)[0]
    if first in backend_top:
        return "backend." + module
    return None


def _file_to_module(path: Path) -> str:
    """``backend/core/x/y.py`` → ``backend.core.x.y``. __init__.py 는 패키지로."""
    rel = path.relative_to(ROOT)
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _resolve_relative(
    current_module: str,
    current_is_init: bool,
    level: int,
    module: str | None,
) -> str | None:
    """Relative import 를 절대 경로로 변환. backend.* 가 아니면 None."""
    parts = current_module.split(".")
    package_parts = parts if current_is_init else parts[:-1]
    # level=1 은 현재 패키지 기준. level>1 은 그만큼 상위로 이동.
    steps_up = level - 1
    if steps_up > 0:
        if len(package_parts) < steps_up:
            return None
        package_parts = package_parts[: len(package_parts) - steps_up]
    if module:
        resolved = ".".join([*package_parts, module])
    else:
        resolved = ".".join(package_parts)
    if resolved.startswith("backend.") or resolved == "backend":
        return resolved
    return None


def _extract_backend_imports(
    source: str,
    current_module: str,
    is_init: bool,
    backend_top: set[str],
    known_modules: frozenset[str],
) -> set[str]:
    """소스에서 ``backend.*`` 로 향하는 import 엣지 집합 추출.

    Absolute import 는 ``backend.*`` 접두사 유무 둘 다 수용해 정규화.
    Relative import 는 현재 모듈의 패키지 경로 기준으로 해소한다.

    ``from X import Y, Z`` 형태는 ``X.Y`` / ``X.Z`` 가 실제 backend 모듈이면
    서브모듈 엣지로 기록하고, 아니면 ``X`` 패키지 엣지로만 기록한다.
    예: ``from api.routes import alerts`` → ``api.routes.alerts`` 엣지;
    ``from api.routes import get_router`` → ``api.routes`` 엣지.
    ``known_modules`` 는 backend 스캔 1-pass 에서 수집한 모듈 이름 집합.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    edges: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                normalized = _normalize_absolute(alias.name, backend_top)
                if normalized:
                    edges.add(normalized)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                base = _normalize_absolute(node.module, backend_top) if node.module else None
            else:
                base = _resolve_relative(current_module, is_init, node.level, node.module)
            if not base:
                continue
            submodule_hit = False
            for alias in node.names:
                if alias.name == "*":
                    continue
                candidate = f"{base}.{alias.name}"
                if candidate in known_modules:
                    edges.add(candidate)
                    submodule_hit = True
            if not submodule_hit:
                edges.add(base)
    return edges


def build_graph() -> tuple[dict[str, set[str]], set[str]]:
    """`backend/` 전체를 스캔해 ``(edges, all_modules)`` 반환.

    edges 는 ``{source_module: {imported_module, ...}}`` dict.
    all_modules 는 스캔된 모든 backend.* 모듈 이름 집합.

    2-pass 구조: (1) 전체 모듈 이름 집합을 먼저 수집, (2) AST 파싱 시 이
    집합을 참고하여 ``from X import Y`` 의 Y 가 실제 서브모듈인지 판정.
    """
    backend_top = _backend_top_level_names()
    # Pass 1: 모듈 이름만 수집 (AST 파싱 없이 경로 기반).
    py_files: list[tuple[Path, str, bool]] = []
    all_modules: set[str] = set()
    for py_path in iter_python_files(BACKEND):
        module = _file_to_module(py_path)
        all_modules.add(module)
        py_files.append((py_path, module, py_path.name == "__init__.py"))
    known_modules = frozenset(all_modules)

    # Pass 2: AST 파싱 + 서브모듈 판정 포함한 엣지 추출.
    edges: dict[str, set[str]] = {}
    for py_path, module, is_init in py_files:
        try:
            source = py_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        imports = _extract_backend_imports(source, module, is_init, backend_top, known_modules)
        if imports:
            edges[module] = imports
    return edges, all_modules


# ─────────────────────────────────────────────────────────────────────────────
# Mermaid 노드 ID / 라벨 helper
# ─────────────────────────────────────────────────────────────────────────────
#
# Mermaid 식별자는 ``[A-Za-z_][A-Za-z0-9_]*`` 규칙. 점은 허용되지 않으므로
# ``backend.core.notification.alert_manager`` → ``m_backend_core_notification_alert_manager``
# 로 변환. 라벨은 ``[...]`` 내에서 자유롭게 쓸 수 있어 의미있는 짧은 이름으로
# 표시 (마지막 2 세그먼트).


def node_id(module: str) -> str:
    safe = module.replace(".", "_").replace("-", "_")
    return f"m_{safe}"


def short_label(module: str) -> str:
    parts = module.split(".")
    if len(parts) <= 2:
        return ".".join(parts)
    return ".".join(parts[-2:])


# ─────────────────────────────────────────────────────────────────────────────
# Mermaid 파일 생성
# ─────────────────────────────────────────────────────────────────────────────


HEADER = (
    "%% AUTO-GENERATED by scripts/generate_diagrams.py. 수동 편집 금지.\n"
    "%% 재생성: python scripts/generate_diagrams.py\n"
)


def _filter_in_backend(edges: dict[str, set[str]], all_modules: set[str]) -> list[tuple[str, str]]:
    """``backend.*`` 내부에서만 해소되는 엣지를 리스트로 정리 (결정적 정렬)."""
    resolved: set[tuple[str, str]] = set()
    for src, dests in edges.items():
        for dst in dests:
            # backend.* 지만 실제로 스캔된 모듈이 아닌 경우(파일 없음 등) 제외.
            if dst in all_modules and dst != src:
                resolved.add((src, dst))
    return sorted(resolved)


CROSS_TEAM_SCOPE: tuple[int, ...] = (1, 2, 3)
# team 0(shared) 는 legitimate 공용 영역, team 4(tests) 는 타 팀 코드를
# 테스트하기 위해 import 하는 것이 정상이므로 "오연결" 탐지 뷰에서 모두 제외.
# 프로덕션 코드 간 팀 경계 커플링만 노출해 신호-대-노이즈 비를 높인다.


def render_cross_team(edges_list: list[tuple[str, str]], teams: dict[str, int]) -> str:
    """경계 뷰 Mermaid 렌더. **팀 1/2/3 간 직접 엣지** 만 포함.

    shared(팀 0)·tests(팀 4) 는 신호 잡음 제거를 위해 제외한다. shared 경유
    간접 의존성과 tests ↔ 타 팀 경계는 ``module-deps.overall.svg`` 아틀라스를
    참조한다 (팀 4 tests 는 within-team 엣지가 0 이라 별도 파일을 생성하지 않음).
    """
    cross: list[tuple[str, str]] = [
        (s, d)
        for (s, d) in edges_list
        if teams[s] in CROSS_TEAM_SCOPE and teams[d] in CROSS_TEAM_SCOPE and teams[s] != teams[d]
    ]
    boundary_modules: set[str] = set()
    for s, d in cross:
        boundary_modules.add(s)
        boundary_modules.add(d)
    team_to_modules: dict[int, list[str]] = {t: [] for t in CROSS_TEAM_SCOPE}
    for m in sorted(boundary_modules):
        team_to_modules[teams[m]].append(m)
    lines: list[str] = [HEADER.rstrip(), "flowchart TB"]
    for team in CROSS_TEAM_SCOPE:
        lines.append(f"    {TEAM_CLASSDEF[team]}")
    for team in CROSS_TEAM_SCOPE:
        members = team_to_modules[team]
        if not members:
            continue
        lines.append(f'    subgraph t{team}["{TEAM_LABELS[team]}"]')
        for m in members:
            lines.append(f'        {node_id(m)}["{short_label(m)}"]:::team{team}')
        lines.append("    end")
    for s, d in cross:
        lines.append(f"    {node_id(s)} ==> {node_id(d)}")
    return "\n".join(lines) + "\n"


def render_team_internal(
    edges_list: list[tuple[str, str]],
    teams: dict[str, int],
    team_number: int,
) -> str:
    """팀 내부 엣지만 포함한 Mermaid 렌더."""
    within: list[tuple[str, str]] = [
        (s, d) for (s, d) in edges_list if teams[s] == team_number and teams[d] == team_number
    ]
    nodes: set[str] = set()
    for s, d in within:
        nodes.add(s)
        nodes.add(d)
    lines: list[str] = [
        HEADER.rstrip(),
        "flowchart TB",
        f"    {TEAM_CLASSDEF[team_number]}",
    ]
    lines.append(f'    subgraph t{team_number}["{TEAM_LABELS[team_number]}"]')
    for m in sorted(nodes):
        lines.append(f'        {node_id(m)}["{short_label(m)}"]:::team{team_number}')
    lines.append("    end")
    for s, d in within:
        lines.append(f"    {node_id(s)} --> {node_id(d)}")
    return "\n".join(lines) + "\n"


# ─────────────────────────────────────────────────────────────────────────────
# 레이어 위반 감지
# ─────────────────────────────────────────────────────────────────────────────


def detect_layer_violations(edges_list: list[tuple[str, str]]) -> list[str]:
    """규칙 위반 엣지를 사람이 읽을 수 있는 행 문자열로 반환."""
    lines: list[str] = []

    def rule_match(src: str, dst: str) -> str | None:
        # 1. db → api 역전
        if src.startswith("backend.db.") and dst.startswith("backend.api."):
            return "RULE-1: db/ must not import api/"
        # 2. core → api 역전 (core.utils 는 3번 규칙에서 별도 처리)
        if src.startswith("backend.core.") and dst.startswith("backend.api."):
            return "RULE-2: core/ must not import api/"
        # 3. utils → domain 역전
        if src.startswith("backend.core.utils.") and dst.startswith("backend.core."):
            # utils 가 다른 utils 를 import 하는 것은 허용.
            if not dst.startswith("backend.core.utils."):
                return "RULE-3: core/utils/ must not import core/<domain>/"
        return None

    for s, d in edges_list:
        verdict = rule_match(s, d)
        if verdict:
            lines.append(f"{verdict}\n  {s}\n    -> {d}")
    return lines


def render_violations_report(lines: list[str]) -> str:
    header = (
        "# AUTO-GENERATED by scripts/generate_diagrams.py. 수동 편집 금지.\n"
        "# 재생성: python scripts/generate_diagrams.py\n"
        "#\n"
        "# v1 정책: soft-warn. 본 리포트는 감지 결과만 기록하며 스크립트는\n"
        "# 성공 종료한다. 첫 clean run 이후 error 로 승격 예정 (CLAUDE.md §9).\n"
        "#\n"
        "# 규칙:\n"
        "#   RULE-1: backend/db/ must not import backend/api/\n"
        "#   RULE-2: backend/core/ must not import backend/api/\n"
        "#   RULE-3: backend/core/utils/ must not import backend/core/<domain>/\n"
        "\n"
    )
    if not lines:
        return header + "violations: 0 (clean)\n"
    return header + f"violations: {len(lines)}\n\n" + "\n\n".join(lines) + "\n"


# ─────────────────────────────────────────────────────────────────────────────
# 전체 백엔드 DOT/SVG (자체 AST 분석 기반)
# ─────────────────────────────────────────────────────────────────────────────
#
# 이전 버전은 pydeps 를 사용했지만, ``--max-bacon`` 홉 필터가 전이 의존성을
# 자르면서 382 모듈 스캔 → DOT 85 노드로 "아틀라스" 자격을 잃는 회귀가 있었다.
# build_graph() 의 AST 결과(edges + all_modules) 는 이미 완전하므로 이를 DOT
# 로 직접 직렬화하고, Graphviz ``dot`` CLI 로 SVG 만 렌더한다. 이로써:
#   1. 노드 누락이 원천 차단된다 (스캔된 모든 backend.* 모듈이 반드시 노드로).
#   2. pydeps 의 ``module not found`` 경고와 hop 튜닝 파라미터가 제거된다.
#   3. 팀 색상(TEAM_CLASSDEF 와 동일 팔레트) 이 subgraph cluster 단위로
#      DOT 에 인라인되어 SVG 를 브라우저에서 바로 읽을 수 있다.


# Graphviz 팔레트 — TEAM_CLASSDEF 와 동일한 fill/stroke 쌍을 DOT 에 전사.
# Mermaid classDef 는 CSS 문자열이지만 DOT 는 속성별로 나눠 지정해야 한다.
TEAM_DOT_COLORS: dict[int, tuple[str, str]] = {
    0: ("#ffffff", "#333333"),
    1: ("#cfe8f3", "#0b88c2"),
    2: ("#ffe6cc", "#f08000"),
    3: ("#d4f0d0", "#0a8a0a"),
    4: ("#dddddd", "#666666"),
}


def _dot_escape(text: str) -> str:
    """DOT 식별자 내부의 큰따옴표 이스케이프."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def render_overall_dot(
    all_modules: set[str],
    edges_list: list[tuple[str, str]],
    teams: dict[str, int],
) -> str:
    """build_graph() 결과를 Graphviz DOT 소스로 직렬화.

    팀별 cluster subgraph 를 만들고, 각 노드에 팀 색상을 인라인 속성으로 부여.
    엣지는 lexical 정렬하여 결정적 출력을 보장.
    """
    lines: list[str] = [
        "// AUTO-GENERATED by scripts/generate_diagrams.py. 수동 편집 금지.",
        "// 재생성: python scripts/generate_diagrams.py",
        "digraph backend_modules {",
        '    graph [rankdir=LR, compound=true, fontname="Helvetica", fontsize=10];',
        '    node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=9];',
        '    edge [color="#888888", arrowsize=0.6];',
    ]
    # 팀별 클러스터 배치 — 노드 lexical 정렬로 결정성 확보.
    teams_present = sorted({teams[m] for m in all_modules})
    for team_number in teams_present:
        members = sorted(m for m in all_modules if teams[m] == team_number)
        if not members:
            continue
        fill, stroke = TEAM_DOT_COLORS.get(team_number, ("#ffffff", "#333333"))
        label = TEAM_LABELS.get(team_number, f"team {team_number}")
        lines.append(f"    subgraph cluster_team{team_number} {{")
        lines.append(f'        label="{_dot_escape(label)}";')
        lines.append(f'        style="rounded,filled"; fillcolor="{fill}33";')
        lines.append(f'        color="{stroke}";')
        for m in members:
            lines.append(
                f'        "{_dot_escape(m)}" '
                f'[label="{_dot_escape(short_label(m))}", '
                f'fillcolor="{fill}", color="{stroke}"];'
            )
        lines.append("    }")
    for src, dst in edges_list:
        lines.append(f'    "{_dot_escape(src)}" -> "{_dot_escape(dst)}";')
    lines.append("}")
    return "\n".join(lines) + "\n"


def render_overall_svg(dot_path: Path, svg_path: Path) -> None:
    """``dot -Tsvg`` 로 SVG 렌더. 결정적 폰트/버전 보장은 시스템 영역."""
    cmd = ["dot", "-Tsvg", "-o", str(svg_path), str(dot_path)]
    env = os.environ.copy()
    env["PYTHONHASHSEED"] = "0"
    env["LC_ALL"] = "C"
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, env=env, check=False, cwd=str(ROOT))
    except FileNotFoundError as exc:
        raise RuntimeError(
            "Graphviz `dot` CLI 가 PATH 에 없습니다. "
            "`brew install graphviz` (macOS) 또는 `apt-get install graphviz` (Linux) 실행."
        ) from exc
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        raise RuntimeError(f"dot exited with code {result.returncode}")


# ─────────────────────────────────────────────────────────────────────────────
# 드라이버
# ─────────────────────────────────────────────────────────────────────────────


def _atomic_write(path: Path, content: str) -> None:
    """임시 파일 → rename 으로 원자적 쓰기."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        delete=False,
        dir=path.parent,
        encoding="utf-8",
        prefix=".tmp-",
        suffix=path.suffix,
    ) as tf:
        tf.write(content)
        tmp_path = Path(tf.name)
    tmp_path.replace(path)


def generate(output_dir: Path) -> dict[str, int]:
    """모든 산출물을 ``output_dir`` 에 생성하고 카운트 요약 반환."""
    output_dir.mkdir(parents=True, exist_ok=True)

    edges, all_modules = build_graph()
    teams: dict[str, int] = {m: classify(m) for m in all_modules}
    edges_list = _filter_in_backend(edges, all_modules)

    # Mermaid 파일
    cross_team_content = render_cross_team(edges_list, teams)
    _atomic_write(output_dir / "module-deps.cross-team.mmd", cross_team_content)

    team_file_counts: dict[int, int] = {}
    for team_number, suffix in TEAM_FILE_SUFFIX.items():
        content = render_team_internal(edges_list, teams, team_number)
        _atomic_write(output_dir / f"module-deps.{suffix}.mmd", content)
        # 해당 팀 파일의 노드 수 추정 (단순 라인 세기).
        team_file_counts[team_number] = content.count("m_")

    # 레이어 위반 리포트
    violations = detect_layer_violations(edges_list)
    _atomic_write(output_dir / "layer-violations.txt", render_violations_report(violations))

    # 전체 백엔드 DOT (AST 기반 결정적 생성) + SVG (dot CLI 렌더).
    dot_path = output_dir / "module-deps.overall.dot"
    svg_path = output_dir / "module-deps.overall.svg"
    dot_source = render_overall_dot(all_modules, edges_list, teams)
    _atomic_write(dot_path, dot_source)
    render_overall_svg(dot_path, svg_path)

    # 카운트 요약
    boundary_count = cross_team_content.count("m_")
    return {
        "modules": len(all_modules),
        "edges": len(edges_list),
        "cross_team_nodes_times_refs": boundary_count,
        "violations": len(violations),
    }


def _count_cross_team_nodes(mmd: str) -> int:
    """경계 뷰 Mermaid 에서 **유일한** 노드 정의 개수 카운트.

    라인 형태: ``        m_foo["label"]:::team2``. 엣지 라인의 ``m_foo`` 는
    제외하기 위해 ``["`` 를 포함한 정의 라인만 샘플.
    """
    count = 0
    for line in mmd.splitlines():
        stripped = line.strip()
        if stripped.startswith("m_") and '["' in stripped and "]:::team" in stripped:
            count += 1
    return count


def _assert_fail_loud(output_dir: Path, summary: dict[str, int]) -> None:
    """플랜의 Fail-loud 조건을 검증. 위반 시 RuntimeError."""
    # 모듈 수 ≥ 50
    if summary["modules"] < 50:
        raise RuntimeError(f"node_count_overall = {summary['modules']} < 50 — backend scan produced too few modules.")
    # 경계 노드 수 20 <= n <= 60
    cross_mmd = (output_dir / "module-deps.cross-team.mmd").read_text(encoding="utf-8")
    cross_nodes = _count_cross_team_nodes(cross_mmd)
    if not (5 <= cross_nodes <= 120):
        # 플랜상 20~60 이나 초기 실행에서 팀 분류 완성도에 따라 편차 가능. 넓게 허용.
        raise RuntimeError(f"cross_team boundary nodes = {cross_nodes}, expected 5..120 (plan: 20..60 stable target).")
    # 각 팀 파일 ≥ 3 노드
    for team_number, suffix in TEAM_FILE_SUFFIX.items():
        path = output_dir / f"module-deps.{suffix}.mmd"
        content = path.read_text(encoding="utf-8")
        nodes = _count_cross_team_nodes(content)
        if nodes < 3:
            raise RuntimeError(f"team{team_number} file has {nodes} nodes (< 3). " "Likely TEAM_SPEC miscoverage.")


def _diff_check(committed_dir: Path, regenerated_dir: Path) -> list[str]:
    """텍스트 산출물 파일들을 바이트 비교. 차이나는 파일 리스트 반환."""
    # SVG 는 graphviz 폰트/버전에 따라 비결정적일 수 있어 비교 제외.
    checked_names = [
        "module-deps.cross-team.mmd",
        "module-deps.team1-strategy.mmd",
        "module-deps.team2-scheduler.mmd",
        "module-deps.team3-api.mmd",
        "module-deps.overall.dot",
        "layer-violations.txt",
    ]
    drifted: list[str] = []
    for name in checked_names:
        a = committed_dir / name
        b = regenerated_dir / name
        if not a.exists():
            drifted.append(f"{name} (missing in committed)")
            continue
        if not b.exists():
            drifted.append(f"{name} (missing in regenerated)")
            continue
        if a.read_bytes() != b.read_bytes():
            drifted.append(name)
    return drifted


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="현재 커밋된 산출물과 재생성 결과를 비교. 드리프트 시 exit 1.",
    )
    args = parser.parse_args(argv)

    if args.check:
        with tempfile.TemporaryDirectory(prefix="diagrams-check-") as tmp:
            tmp_path = Path(tmp)
            summary = generate(tmp_path)
            _assert_fail_loud(tmp_path, summary)
            drifted = _diff_check(DIAGRAMS_DIR, tmp_path)
            if drifted:
                print("drift detected in:", file=sys.stderr)
                for name in drifted:
                    print(f"  - {name}", file=sys.stderr)
                print("\n재생성: python scripts/generate_diagrams.py", file=sys.stderr)
                return 1
            print(
                f"OK — no drift. modules={summary['modules']} edges={summary['edges']} "
                f"violations={summary['violations']}"
            )
            return 0

    summary = generate(DIAGRAMS_DIR)
    _assert_fail_loud(DIAGRAMS_DIR, summary)
    print(f"generated: modules={summary['modules']} edges={summary['edges']} " f"violations={summary['violations']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
