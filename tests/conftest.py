"""Shared fixtures. The database is built once per test session for modules that opt in."""
import pytest

from olist_risk.build_db import build


@pytest.fixture(scope="session")
def built_db(tmp_path_factory):
    return build(tmp_path_factory.mktemp("session_db") / "olist_session.db")
