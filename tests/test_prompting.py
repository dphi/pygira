"""Tests for interactive terminal selection."""

from unittest.mock import patch

import click
import pytest
from click.testing import CliRunner
from prompt_toolkit.completion import FuzzyCompleter

from pygira.prompting import TypedAddress, search_select, search_select_or_ip


def test_search_select_builds_fuzzy_arrow_key_prompt() -> None:
    with (
        patch("pygira.prompting._is_interactive_terminal", return_value=True),
        patch("pygira.prompting.terminal_prompt", return_value="Office") as prompt,
    ):
        selected = search_select(
            "Location",
            [("Home", "home"), ("Office", "office")],
        )

    assert selected == "office"
    kwargs = prompt.call_args.kwargs
    assert isinstance(kwargs["completer"], FuzzyCompleter)
    assert kwargs["complete_while_typing"] is True
    assert kwargs["pre_run"] is not None


def test_search_select_accepts_exact_label_when_redirected() -> None:
    @click.command()
    def choose() -> None:
        click.echo(search_select("Location", [("Home", "home"), ("Office", "office")]))

    result = CliRunner().invoke(choose, input="office\n")

    assert result.exit_code == 0, result.output
    assert result.output.endswith("office\n")


def test_search_select_or_ip_accepts_ip_in_same_prompt() -> None:
    @click.command()
    def choose() -> None:
        selected = search_select_or_ip("Location or IP", [("Home", "home")])
        assert isinstance(selected, TypedAddress)
        click.echo(selected.value)

    result = CliRunner().invoke(choose, input="192.0.2.40\n")

    assert result.exit_code == 0, result.output
    assert result.output.endswith("192.0.2.40\n")


def test_search_select_or_ip_rejects_unmatched_search_text() -> None:
    @click.command()
    def choose() -> None:
        selected = search_select_or_ip("Location or IP", [("Home", "home")])
        assert isinstance(selected, TypedAddress)
        click.echo(selected.value)

    result = CliRunner().invoke(choose, input="not-a-location\n192.0.2.40\n")

    assert result.exit_code == 0, result.output
    assert "Error: Select a match or type an IP address." in result.output
    assert result.output.endswith("192.0.2.40\n")


def test_search_select_translates_cancel_to_click_abort() -> None:
    with (
        patch("pygira.prompting._is_interactive_terminal", return_value=True),
        patch("pygira.prompting.terminal_prompt", side_effect=KeyboardInterrupt),
        pytest.raises(click.Abort),
    ):
        search_select("Location", [("Home", "home")])
