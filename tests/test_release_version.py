import pytest

from scripts.release_version import bump_version, current_version


@pytest.fixture
def project(tmp_path):
    (tmp_path / "src/anyllama").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "anyllama"\nversion = "1.9.9"\n')
    (tmp_path / "src/anyllama/__init__.py").write_text('__version__ = "1.9.9"\n')
    (tmp_path / "uv.lock").write_text(
        'version = 1\n[[package]]\nname = "anyllama"\nversion = "1.9.9"\n'
        '[[package]]\nname = "dependency"\nversion = "7.8.9"\n'
    )
    return tmp_path


@pytest.mark.parametrize("part,expected", [("patch", "1.9.10"), ("minor", "1.10.0"), ("major", "2.0.0")])
def test_bump_updates_all_versions_without_changing_dependencies(project, part, expected):
    assert bump_version(part, project) == expected
    assert current_version(project) == expected
    assert 'name = "dependency"\nversion = "7.8.9"' in (project / "uv.lock").read_text()


def test_mismatched_version_prevents_any_changes(project):
    source = project / "src/anyllama/__init__.py"
    source.write_text('__version__ = "2.0.0"\n')
    before = (project / "pyproject.toml").read_bytes()
    with pytest.raises(ValueError, match="match"):
        bump_version("patch", project)
    assert (project / "pyproject.toml").read_bytes() == before
