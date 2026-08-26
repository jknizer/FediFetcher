import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).parent.parent


def declared():
    """What pyproject says we need, as {name: version specifier}"""
    with (ROOT / "pyproject.toml").open("rb") as file:
        pyproject = tomllib.load(file)
    return {
        canonicalize_name(dep.name): dep.specifier
        for dep in map(Requirement, pyproject["project"]["dependencies"])
    }


def pinned():
    """What requirements.txt installs, as {name: version}"""
    lines = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    requirements = [Requirement(line) for line in lines if line.strip()]
    return {
        canonicalize_name(req.name): str(next(iter(req.specifier)).version)
        for req in requirements
    }


def test_requirements_are_all_exact_pins():
    lines = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    for line in filter(str.strip, lines):
        specifier = list(Requirement(line).specifier)
        assert len(specifier) == 1 and specifier[0].operator == "==", (
            f"{line!r} is not an exact pin, but requirements.txt is a lock file"
        )


def test_every_declared_dependency_is_pinned():
    missing = declared().keys() - pinned().keys()
    assert not missing, f"declared in pyproject but not pinned in requirements.txt: {sorted(missing)}"


@pytest.mark.parametrize("name", sorted(declared()))
def test_pinned_version_satisfies_declared_bounds(name):
    version = pinned()[name]
    specifier = declared()[name]
    assert specifier.contains(version), (
        f"requirements.txt pins {name}=={version}, which pyproject.toml does not "
        f"allow ({name}{specifier}). Raise the bound if the new version is required."
    )
