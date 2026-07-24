"""Tests for interactive terminal selection."""

from unittest.mock import patch

import click
import pytest
from click.testing import CliRunner

from pygira.prompting import search_select


def test_search_select_builds_fuzzy_arrow_key_prompt() -> None:
    with (
        patch("pygira.prompting._is_interactive_terminal", return_value=True),
        patch("pygira.prompting.inquirer.fuzzy") as fuzzy,
    ):
        fuzzy.return_value.execute.return_value = "office"
        selected = search_select(
            "Location",
            [("Home", "home"), ("Office", "office")],
        )

    assert selected == "office"
    kwargs = fuzzy.call_args.kwargs
    assert [choice.name for choice in kwargs["choices"]] == ["Home", "Office"]
    assert [choice.value for choice in kwargs["choices"]] == ["home", "office"]
    assert "type to search" in kwargs["instruction"]
    assert kwargs["cycle"] is True


def test_search_select_accepts_exact_label_when_redirected() -> None:
    @click.command()
    def choose() -> None:
        click.echo(search_select("Location", [("Home", "home"), ("Office", "office")]))

    result = CliRunner().invoke(choose, input="office\n")

    assert result.exit_code == 0, result.output
    assert result.output.endswith("office\n")


def test_search_select_translates_cancel_to_click_abort() -> None:
    with (
        patch("pygira.prompting._is_interactive_terminal", return_value=True),
        patch("pygira.prompting.inquirer.fuzzy") as fuzzy,
    ):
        fuzzy.return_value.execute.side_effect = KeyboardInterrupt
        with pytest.raises(click.Abort):
            search_select("Location", [("Home", "home")])
