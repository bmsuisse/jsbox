import pytest

from jsbox import Runtime


@pytest.fixture
def rt():
    return Runtime()
