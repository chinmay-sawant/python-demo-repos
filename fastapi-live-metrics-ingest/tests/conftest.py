from unittest.mock import AsyncMock, MagicMock

import pytest
from app.dependencies import get_session
from app.main import app
from httpx import ASGITransport, AsyncClient


@pytest.fixture(autouse=True)
def _setup_app_state():
    app.state.settings = MagicMock()
    app.state.session_factory = MagicMock()
    app.state.engine = MagicMock()


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
def mock_session():
    mock = AsyncMock()
    app.dependency_overrides[get_session] = lambda: mock
    yield mock
    app.dependency_overrides.clear()
