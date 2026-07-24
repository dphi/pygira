"""Reusable interactive terminal prompts."""

import sys
from collections.abc import Sequence
from typing import TypeVar, cast

import click
from InquirerPy import inquirer
from InquirerPy.base.control import Choice

T = TypeVar("T")


def _is_interactive_terminal() -> bool:
    """Return whether fuzzy full-screen interaction is available."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def search_select(message: str, options: Sequence[tuple[str, T]]) -> T:
    """Select a value by fuzzy search or arrow keys.

    Exact-label input remains available when stdin/stdout are redirected.
    """
    if not options:
        msg = f"{message} has no available choices"
        raise click.UsageError(msg)

    if not _is_interactive_terminal():
        labels = [label for label, _value in options]
        selected = click.prompt(
            message,
            type=click.Choice(labels, case_sensitive=False),
        )
        values = {label.casefold(): value for label, value in options}
        return values[selected.casefold()]

    choices = [Choice(value=value, name=label) for label, value in options]
    try:
        result = inquirer.fuzzy(
            message=message,
            choices=choices,
            instruction="(type to search, ↑/↓ to move, Enter to select)",
            match_exact=True,
            cycle=True,
        ).execute()
    except (EOFError, KeyboardInterrupt) as exc:
        raise click.Abort from exc
    return cast("T", result)
