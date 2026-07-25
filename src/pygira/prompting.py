"""Reusable interactive terminal prompts."""

import sys
from collections.abc import Sequence
from dataclasses import dataclass
from ipaddress import ip_address
from typing import TypeVar, cast

import click
from prompt_toolkit import prompt as terminal_prompt
from prompt_toolkit.application import get_app
from prompt_toolkit.completion import FuzzyCompleter, WordCompleter
from prompt_toolkit.validation import Validator

T = TypeVar("T")


@dataclass(frozen=True)
class TypedAddress:
    """An IP address entered instead of selecting a configured item."""

    value: str


def _is_interactive_terminal() -> bool:
    """Return whether fuzzy full-screen interaction is available."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def _valid_ip(value: str) -> bool:
    try:
        ip_address(value)
    except ValueError:
        return False
    return True


def _resolve_input(
    entered: str,
    options: Sequence[tuple[str, T]],
    *,
    allow_ip: bool,
) -> T | TypedAddress | None:
    values = {label.casefold(): value for label, value in options}
    normalized = entered.strip()
    if normalized.casefold() in values:
        return values[normalized.casefold()]
    if allow_ip and _valid_ip(normalized):
        return TypedAddress(normalized)
    return None


def _show_completions() -> None:
    """Open the completion menu as soon as the prompt starts."""
    get_app().current_buffer.start_completion(select_first=False)


def _select(
    message: str,
    options: Sequence[tuple[str, T]],
    *,
    allow_ip: bool,
) -> T | TypedAddress:
    if not options and not allow_ip:
        msg = f"{message} has no available choices"
        raise click.UsageError(msg)
    if len(options) == 1 and not allow_ip:
        return options[0][1]

    guidance = "Select a match"
    if allow_ip:
        guidance += " or type an IP address"

    if not _is_interactive_terminal():
        while True:
            entered = click.prompt(message, type=str)
            result = _resolve_input(entered, options, allow_ip=allow_ip)
            if result is not None:
                return result
            click.echo(f"Error: {guidance}.", err=True)

    labels = [label for label, _value in options]
    completer = FuzzyCompleter(WordCompleter(labels, sentence=True))
    validator = Validator.from_callable(
        lambda entered: _resolve_input(entered, options, allow_ip=allow_ip) is not None,
        error_message=guidance,
        move_cursor_to_end=True,
    )
    try:
        entered = terminal_prompt(
            f"{message}: ",
            completer=completer,
            complete_while_typing=True,
            validator=validator,
            pre_run=_show_completions if labels else None,
            reserve_space_for_menu=min(len(labels), 8),
        )
    except (EOFError, KeyboardInterrupt) as exc:
        raise click.Abort from exc
    return cast("T | TypedAddress", _resolve_input(entered, options, allow_ip=allow_ip))


def search_select(message: str, options: Sequence[tuple[str, T]]) -> T:
    """Select a configured value by fuzzy search or arrow keys."""
    return cast("T", _select(message, options, allow_ip=False))


def search_select_or_ip(
    message: str,
    options: Sequence[tuple[str, T]],
) -> T | TypedAddress:
    """Select a configured value or type a valid IP address in the same prompt."""
    return _select(message, options, allow_ip=True)
