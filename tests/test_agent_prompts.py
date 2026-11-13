from pathlib import Path

import pytest

from settlement.agents.errors import PromptError
from settlement.agents.prompts import load_prompt


def write(root: Path, name: str, version: str, text: str) -> None:
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{version}.md").write_text(text, encoding="utf-8")


def test_loads_the_name_version_and_text(tmp_path: Path) -> None:
    write(tmp_path, "classifier", "v1", "You classify settlement breaks.")

    prompt = load_prompt(tmp_path, "classifier", "v1")

    assert prompt.name == "classifier"
    assert prompt.version == "v1"
    assert prompt.text == "You classify settlement breaks."


def test_the_same_text_gives_the_same_fingerprint(tmp_path: Path) -> None:
    write(tmp_path, "first", "v1", "Hello")
    write(tmp_path, "second", "v1", "Hello")

    first = load_prompt(tmp_path, "first", "v1")
    second = load_prompt(tmp_path, "second", "v1")

    assert first.fingerprint == second.fingerprint


def test_one_changed_character_changes_the_fingerprint(tmp_path: Path) -> None:
    write(tmp_path, "classifier", "v1", "Hello")
    before = load_prompt(tmp_path, "classifier", "v1").fingerprint

    write(tmp_path, "classifier", "v1", "Hello!")  # edited without a new version

    assert load_prompt(tmp_path, "classifier", "v1").fingerprint != before


def test_a_missing_file_is_a_prompt_error(tmp_path: Path) -> None:
    with pytest.raises(PromptError, match="classifier/v9.md: cannot read file"):
        load_prompt(tmp_path, "classifier", "v9")


def test_an_empty_file_is_a_prompt_error(tmp_path: Path) -> None:
    write(tmp_path, "classifier", "v1", "   \n")

    with pytest.raises(PromptError, match="file is empty"):
        load_prompt(tmp_path, "classifier", "v1")


@pytest.mark.parametrize(
    "name, version",
    [("../secrets", "v1"), ("classifier", "latest"), ("classifier", "v0")],
)
def test_names_and_versions_must_be_plain(tmp_path: Path, name: str, version: str) -> None:
    with pytest.raises(PromptError):
        load_prompt(tmp_path, name, version)
