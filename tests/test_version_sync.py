import re
import tomllib
from pathlib import Path

from quicktype.constants import APP_VERSION

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_pyproject_version_matches_app_version() -> None:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["version"] == APP_VERSION


def test_windows_version_info_matches_app_version() -> None:
    text = (REPO_ROOT / "packaging" / "windows_version_info.txt").read_text(encoding="utf-8")
    expected_tuple = ", ".join([*APP_VERSION.split("."), "0"])

    file_version = re.search(r"StringStruct\(u'FileVersion', u'([^']+)'\)", text)
    product_version = re.search(r"StringStruct\(u'ProductVersion', u'([^']+)'\)", text)
    filevers = re.search(r"filevers=\(([^)]+)\)", text)
    prodvers = re.search(r"prodvers=\(([^)]+)\)", text)
    assert file_version is not None
    assert product_version is not None
    assert filevers is not None
    assert prodvers is not None

    assert file_version.group(1) == APP_VERSION
    assert product_version.group(1) == APP_VERSION
    assert filevers.group(1) == expected_tuple
    assert prodvers.group(1) == expected_tuple
