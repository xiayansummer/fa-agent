import os

# Stub all required Settings fields so tests can run without .env
_REQUIRED_STUBS = {
    "MYSQL_URL": "mysql+aiomysql://root:test@localhost:3306/test",
    "REDIS_URL": "redis://localhost:6379/0",
    "WECHAT_APPID": "test",
    "WECHAT_SECRET": "test",
    "JWT_SECRET_KEY": "test-secret-key-32-chars-minimum",
    "AI_API_KEY": "test",
    "TAVILY_API_KEY": "test",
    "QMINGPIAN_TOKEN": "test",
    "TENCENT_SECRET_ID": "test",
    "TENCENT_SECRET_KEY": "test",
    "TENCENT_MEETING_APP_ID": "test",
    "TENCENT_MEETING_SECRET_ID": "test",
    "TENCENT_MEETING_SECRET_KEY": "test",
    "TOKEN_ENCRYPT_KEY": "j5HMo36zfzdv1pQbvtgPvlxr2mMXuZN8nRtFgUnUM6E=",  # valid Fernet key for tests only
}
for k, v in _REQUIRED_STUBS.items():
    os.environ.setdefault(k, v)

# THEN do project imports
import pytest
import pytest_asyncio
import asyncio
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from database import Base
import models  # noqa: F401 — ensures all models register with Base.metadata

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

@pytest_asyncio.fixture(loop_scope="session")
async def override_db(db_session):
    from main import app
    from database import get_db

    async def _override():
        yield db_session

    app.dependency_overrides[get_db] = _override
    yield
    app.dependency_overrides.clear()


@pytest_asyncio.fixture(loop_scope="session")
async def authed_client(override_db, db_session):
    """A test client with a JWT-authenticated IR user. Returns (client, user)."""
    from httpx import AsyncClient, ASGITransport
    from main import app
    from auth.jwt import create_token
    from models.ir_users import IRUser

    user = IRUser(name="Test IR", phone="13800000001", role="ir", wechat_openid="test_openid_xxx")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    token = create_token(user.id, user.role)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}) as client:
        yield client, user
    # cleanup: delete the user so it doesn't linger across test sessions
    await db_session.delete(user)
    await db_session.commit()
