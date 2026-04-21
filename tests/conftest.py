import pytest
import pytest_asyncio
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from database import Base

TEST_DB_URL = os.environ.get(
    "TEST_DB_URL",
    "mysql+aiomysql://root:Investarget%402017@39.107.14.53:3306/fa_agent_test",
)

@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def db_engine():
    engine = create_async_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()

@pytest_asyncio.fixture(loop_scope="session")
async def db_session(db_engine):
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
        await session.rollback()
