"""
AQTS 데이터베이스 연결 관리 모듈

PostgreSQL (TimescaleDB): SQLAlchemy AsyncSession
MongoDB: Motor AsyncIOMotorClient
Redis: aioredis
"""

from typing import AsyncGenerator

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from config.settings import get_settings

settings = get_settings()


# ══════════════════════════════════════
# SQLAlchemy Base
# ══════════════════════════════════════
class Base(DeclarativeBase):
    """SQLAlchemy ORM 베이스 클래스"""

    pass


# ══════════════════════════════════════
# PostgreSQL (TimescaleDB) Engine
# ══════════════════════════════════════
engine = create_async_engine(
    settings.db.async_url,
    echo=False,
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,
    pool_recycle=3600,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 의존성 주입용 DB 세션 제너레이터"""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ══════════════════════════════════════
# MongoDB Connection
# ══════════════════════════════════════
class MongoDBManager:
    """MongoDB 연결 관리자"""

    _client: AsyncIOMotorClient = None
    _db: AsyncIOMotorDatabase = None

    @classmethod
    async def connect(cls) -> None:
        cls._client = AsyncIOMotorClient(settings.mongo.uri)
        cls._db = cls._client[settings.mongo.db]

    @classmethod
    async def disconnect(cls) -> None:
        if cls._client:
            cls._client.close()

    @classmethod
    def get_db(cls) -> AsyncIOMotorDatabase:
        if cls._db is None:
            raise RuntimeError("MongoDB not connected. Call connect() first.")
        return cls._db

    @classmethod
    def get_collection(cls, name: str):
        return cls.get_db()[name]


# ══════════════════════════════════════
# Redis Connection
# ══════════════════════════════════════
class RedisManager:
    """Redis 연결 관리자"""

    _client: Redis = None

    @classmethod
    async def connect(cls) -> None:
        cls._client = Redis.from_url(
            settings.redis.url,
            decode_responses=True,
            max_connections=20,
        )

    @classmethod
    async def disconnect(cls) -> None:
        if cls._client:
            await cls._client.close()

    @classmethod
    def get_client(cls) -> Redis:
        if cls._client is None:
            raise RuntimeError("Redis not connected. Call connect() first.")
        return cls._client
