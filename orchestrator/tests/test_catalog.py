import pytest
from ephemeral.docker.catalog import CATALOG, get_profile
from ephemeral.docker.errors import UnknownProfileError


def test_catalog_nonempty():
    assert len(CATALOG) >= 2


def test_catalog_names():
    names = {p.name for p in CATALOG}
    assert "python-base" in names
    assert "python-data" in names


def test_get_profile_found():
    p = get_profile("python-base")
    assert p.name == "python-base"
    assert p.image == "python:3.11-slim"


def test_get_profile_missing():
    with pytest.raises(UnknownProfileError):
        get_profile("does-not-exist")


def test_profile_has_required_fields():
    for p in CATALOG:
        assert p.image
        assert p.description
        assert isinstance(p.packages, list)
        assert isinstance(p.typical_use_cases, list)
        assert p.size_mb > 0
