import pytest

from cohort.catalog import DB_PATH


def pytest_collection_modifyitems(config, items):
    if not DB_PATH.exists():
        skip = pytest.mark.skip(reason="data/cohort.sqlite missing; run `make data`")
        for item in items:
            if "needs_db" in item.keywords:
                item.add_marker(skip)


def pytest_configure(config):
    config.addinivalue_line("markers", "needs_db: requires the built SQLite database")
