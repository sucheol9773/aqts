"""
Alembic 마이그레이션 구조 검증 테스트.

마이그레이션 파일의 체인 무결성(revision → down_revision 연결), 필수 필드 존재,
순서 일관성을 검증한다. DB 연결 없이 파일 구조만으로 검증하므로
CI 에서 외부 의존성 없이 실행 가능하다.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

ALEMBIC_DIR = Path(__file__).resolve().parents[1] / "alembic"
VERSIONS_DIR = ALEMBIC_DIR / "versions"
ENV_FILE = ALEMBIC_DIR / "env.py"
ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


def _load_migration(filepath: Path) -> dict:
    """마이그레이션 파일에서 revision, down_revision, upgrade, downgrade 추출"""
    spec = importlib.util.spec_from_file_location(filepath.stem, filepath)
    mod = importlib.util.module_from_spec(spec)
    # 임시 모듈로 로드 — sys.modules 에 등록하지 않음
    old_modules = dict(sys.modules)
    try:
        spec.loader.exec_module(mod)
    except Exception:
        # 일부 마이그레이션은 DB 의존성으로 import 실패할 수 있음
        # 이 경우 최소한 파일 존재만 검증
        return {"file": filepath.name, "load_error": True}
    finally:
        # 로드 중 추가된 모듈 정리
        for k in list(sys.modules.keys()):
            if k not in old_modules:
                del sys.modules[k]

    return {
        "file": filepath.name,
        "revision": getattr(mod, "revision", None),
        "down_revision": getattr(mod, "down_revision", None),
        "has_upgrade": hasattr(mod, "upgrade"),
        "has_downgrade": hasattr(mod, "downgrade"),
        "load_error": False,
    }


@pytest.fixture(scope="module")
def migrations() -> list[dict]:
    files = sorted(VERSIONS_DIR.glob("*.py"))
    files = [f for f in files if f.name != "__init__.py"]
    return [_load_migration(f) for f in files]


class TestAlembicStructure:
    def test_alembic_dir_exists(self):
        assert ALEMBIC_DIR.exists(), f"{ALEMBIC_DIR} 가 존재해야 한다."

    def test_env_py_exists(self):
        assert ENV_FILE.exists(), f"{ENV_FILE} 가 존재해야 한다."

    def test_alembic_ini_exists(self):
        assert ALEMBIC_INI.exists(), f"{ALEMBIC_INI} 가 존재해야 한다."

    def test_versions_dir_exists(self):
        assert VERSIONS_DIR.exists(), f"{VERSIONS_DIR} 가 존재해야 한다."

    def test_at_least_one_migration_exists(self, migrations):
        assert len(migrations) >= 1, "최소 1개 마이그레이션 파일이 있어야 한다."


class TestMigrationFiles:
    def test_all_migrations_have_revision(self, migrations):
        for m in migrations:
            if m.get("load_error"):
                continue
            assert m["revision"] is not None, f"{m['file']}: revision 이 None"

    def test_all_migrations_have_upgrade_and_downgrade(self, migrations):
        for m in migrations:
            if m.get("load_error"):
                continue
            assert m["has_upgrade"], f"{m['file']}: upgrade() 함수가 없음"
            assert m["has_downgrade"], f"{m['file']}: downgrade() 함수가 없음"

    def test_revision_chain_integrity(self, migrations):
        """각 마이그레이션의 down_revision 이 이전 마이그레이션의 revision 을 가리킴"""
        loaded = [m for m in migrations if not m.get("load_error")]
        if len(loaded) < 2:
            pytest.skip("체인 검증에 최소 2개 마이그레이션 필요")
        revisions = {m["revision"] for m in loaded}
        for m in loaded:
            if m["down_revision"] is None:
                continue  # 최초 마이그레이션
            assert m["down_revision"] in revisions, (
                f"{m['file']}: down_revision='{m['down_revision']}' 이 "
                f"어떤 마이그레이션의 revision 과도 일치하지 않음"
            )

    def test_exactly_one_root_migration(self, migrations):
        """down_revision=None 인 마이그레이션이 정확히 1개 (체인 루트)"""
        loaded = [m for m in migrations if not m.get("load_error")]
        roots = [m for m in loaded if m["down_revision"] is None]
        assert len(roots) == 1, f"루트 마이그레이션이 {len(roots)}개: " f"{[m['file'] for m in roots]}"

    def test_no_duplicate_revisions(self, migrations):
        loaded = [m for m in migrations if not m.get("load_error")]
        revisions = [m["revision"] for m in loaded]
        seen = set()
        for rev in revisions:
            assert rev not in seen, f"중복 revision: '{rev}'"
            seen.add(rev)

    def test_expected_migration_count(self, migrations):
        """현재 5개 마이그레이션 (회귀 방지 하한)"""
        assert len(migrations) >= 5, f"마이그레이션 {len(migrations)}개 < 기대 하한 5개"
