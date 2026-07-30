import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, MagicMock
from app.main import app
from app.dependencies import get_session

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
