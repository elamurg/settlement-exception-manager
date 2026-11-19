"""Load a versioned prompt file: prompts/<agent>/<version>.md."""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from settlement.agents.errors import PromptError

_NAME = re.compile(r"[a-z][a-z0-9_]*")
_VERSION = re.compile(r"v[1-9][0-9]*")


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    text: str

    @property
    def fingerprint(self) -> str:
        # The first 12 characters of the text's SHA-256 hash. One changed character changes it.
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()[:12]


def load_prompt(root: Path, name: str, version: str) -> Prompt:
    source = f"{name}/{version}.md"
    if not _NAME.fullmatch(name):
        raise PromptError(source, "agent name must be lowercase letters, digits and _")
    if not _VERSION.fullmatch(version):
        raise PromptError(source, "version must look like v1, v2, v3...")
    try:
        text = (root / name / f"{version}.md").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise PromptError(source, f"cannot read file: {e}") from None
    if not text.strip():
        raise PromptError(source, "file is empty")
    return Prompt(name=name, version=version, text=text)
