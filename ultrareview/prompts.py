"""Prompt templates: loading, placeholder rendering and vocabulary checks."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import re
from typing import Mapping, Tuple

from . import PROMPTS_DIR
from .errors import ConfigError

PLACEHOLDER = re.compile(r'{{\s*([A-Z0-9_]+)\s*}}')
FORBIDDEN_WORDS = ('attack', 'adversarial', 'exploit')


class PromptError(ConfigError):
    """A template is missing, malformed or still contains placeholders."""


@lru_cache(maxsize=64)
def load_template(name: str, prompts_dir: Path = PROMPTS_DIR) -> str:
    path = prompts_dir / name
    try:
        return path.read_text(encoding='utf-8')
    except OSError as exc:
        raise PromptError(f'prompt template {name!r} not found under {prompts_dir}') from exc


def placeholders(template: str) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(PLACEHOLDER.findall(template)))


def render(template: str, context: Mapping[str, str]) -> str:
    def substitute(match: 're.Match[str]') -> str:
        key = match.group(1)
        if key not in context:
            raise PromptError(f'no value for placeholder {key}')
        return str(context[key])
    rendered = PLACEHOLDER.sub(substitute, template)
    leftover = PLACEHOLDER.findall(rendered)
    if leftover:
        raise PromptError(f'placeholders left unrendered: {", ".join(leftover)}')
    return rendered


def forbidden_words(text: str) -> Tuple[str, ...]:
    lowered = text.lower()
    return tuple(word for word in FORBIDDEN_WORDS if re.search(rf'\b{word}', lowered))


def render_template(name: str, context: Mapping[str, str], prompts_dir: Path = PROMPTS_DIR) -> str:
    return render(load_template(name, prompts_dir), context)
