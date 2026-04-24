from collections.abc import Generator

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, delete

from app.core.config import settings
from app.core.db import engine, init_db
from app.main import app
from app.models import Item, TeamMember, User
from tests.utils.user import authentication_cookies_from_email
from tests.utils.utils import get_superuser_cookies


@pytest.fixture(scope="session", autouse=True)
def db() -> Generator[Session, None, None]:
    with Session(engine) as session:
        init_db(session)
        yield session
        # Clean up in FK-safe order.
        session.execute(delete(TeamMember))
        session.execute(delete(Item))
        session.execute(delete(User))
        session.commit()


@pytest.fixture(scope="module")
def client() -> Generator[TestClient, None, None]:
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def superuser_cookies(client: TestClient) -> httpx.Cookies:
    return get_superuser_cookies(client)


@pytest.fixture(scope="module")
def normal_user_cookies(client: TestClient, db: Session) -> httpx.Cookies:
    return authentication_cookies_from_email(
        client=client, email=settings.EMAIL_TEST_USER, db=db
    )
