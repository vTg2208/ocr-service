"""Each API test gets an isolated in-process quota, just as it gets an isolated DB."""
import pytest


@pytest.fixture(autouse=True)
def clear_request_quotas():
    from app.main import app
    node = app.middleware_stack
    while node is not None:
        if hasattr(node, "hits"):
            node.hits.clear()
        node = getattr(node, "app", None)
