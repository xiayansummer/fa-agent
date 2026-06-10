import os
import re
import sys
from uuid import uuid4

# Stub all required Settings fields so tests can run without .env
_REQUIRED_STUBS = {
    "MYSQL_URL": "mysql+aiomysql://root:changeme@127.0.0.1:3307/fa_agent_test",
    "REDIS_URL": "redis://localhost:6379/0",
    "WECHAT_APPID": "test",
    "WECHAT_SECRET": "test",
    "JWT_SECRET_KEY": "test-secret-key-32-chars-minimum",
    "AI_API_KEY": "test",
    "TAVILY_API_KEY": "test",
    "QMINGPIAN_TOKEN": "test",
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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from database import Base
import models  # noqa: F401 — ensures all models register with Base.metadata


def _test_database_urls() -> tuple[str, str, str]:
    """Return (server_url, database_url, database_name) for an isolated schema.

    Defaults to the local Docker MySQL exposed by docker-compose on 127.0.0.1:3307.
    If TEST_DB_URL is supplied, only its server credentials are reused; tests still
    run in a freshly-created schema suffixed with a random id.
    """
    source_url = os.environ.get("TEST_DB_URL")
    if source_url:
        url = make_url(source_url)
        prefix = url.database or "fa_agent_test"
    else:
        password = (
            os.environ.get("MYSQL_ROOT_PASSWORD")
            or _env_file_value("MYSQL_ROOT_PASSWORD")
            or "changeme"
        )
        url = URL.create(
            "mysql+aiomysql",
            username="root",
            password=password,
            host="127.0.0.1",
            port=3307,
        )
        prefix = os.environ.get("TEST_DB_NAME_PREFIX", "fa_agent_test")

    prefix = re.sub(r"[^0-9A-Za-z_]", "_", prefix)[:48].strip("_") or "fa_agent_test"
    if "test" not in prefix.lower():
        raise RuntimeError(f"Refusing to create test database with unsafe prefix: {prefix!r}")

    database_name = f"{prefix}_{uuid4().hex[:12]}"
    server_url = str(url.set(database=None))
    database_url = str(url.set(database=database_name))
    return server_url, database_url, database_name


def _env_file_value(key: str) -> str | None:
    repo_root = os.path.dirname(os.path.dirname(__file__))
    try:
        with open(os.path.join(repo_root, ".env"), encoding="utf-8") as env_file:
            for line in env_file:
                if line.startswith(f"{key}="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except FileNotFoundError:
        return None
    return None


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def db_engine():
    server_url, database_url, database_name = _test_database_urls()
    admin_engine = create_async_engine(server_url, isolation_level="AUTOCOMMIT")
    async with admin_engine.connect() as conn:
        await conn.execute(text(
            f"CREATE DATABASE `{database_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        ))
    await admin_engine.dispose()

    engine = create_async_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        await engine.dispose()
        cleanup_engine = create_async_engine(server_url, isolation_level="AUTOCOMMIT")
        async with cleanup_engine.connect() as conn:
            await conn.execute(text(f"DROP DATABASE IF EXISTS `{database_name}`"))
        await cleanup_engine.dispose()

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
