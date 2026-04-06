"""
Alembic 마이그레이션 환경 설정

DB 접속 정보는 config/settings.py의 DatabaseSettings에서 로드한다.
환경변수(.env)를 통해 DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD를 설정한다.

사용법:
  # 새 마이그레이션 생성
  cd backend && alembic revision --autogenerate -m "설명"

  # 마이그레이션 적용
  cd backend && alembic upgrade head

  # 한 단계 롤백
  cd backend && alembic downgrade -1

  # 현재 상태 확인
  cd backend && alembic current
"""

import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# backend/ 디렉토리를 sys.path에 추가하여 config.settings 임포트 가능
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import get_settings  # noqa: E402
from db.database import Base  # noqa: E402

# Alembic Config 객체
config = context.config

# logging 설정
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# SQLAlchemy MetaData (autogenerate 대상)
target_metadata = Base.metadata

# settings.py에서 동기 DB URL 주입
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.db.sync_url)


def run_migrations_offline() -> None:
    """오프라인 모드 마이그레이션 (SQL 스크립트 생성만)

    DB 연결 없이 마이그레이션 SQL을 stdout에 출력한다.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """온라인 모드 마이그레이션 (DB 직접 연결)

    DB에 연결하여 마이그레이션을 실행한다.
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
