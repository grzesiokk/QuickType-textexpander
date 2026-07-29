from datetime import datetime

from quicktype.template_engine import inspect_template, render_template

NOW = datetime(2026, 7, 28, 14, 5, 9)


def test_renders_builtin_variables_and_custom_formats() -> None:
    rendered = render_template(
        "{{date}}|{{date:%Y-%m-%d}}|{{time}}|{{time:%H:%M:%S}}|{{clipboard}}",
        now=NOW,
        clipboard_text="clip",
    )
    assert rendered.text == "28.07.2026|2026-07-28|14:05|14:05:09|clip"
    assert rendered.issues == ()


def test_cursor_marker_is_removed_and_reports_distance_from_end() -> None:
    rendered = render_template("Hello {{cursor}}world", now=NOW)
    assert rendered.text == "Hello world"
    assert rendered.cursor_from_end == 5


def test_unknown_and_invalid_tokens_remain_literal() -> None:
    rendered = render_template("{{unknown}} {{date:}}", now=NOW)
    assert rendered.text == "{{unknown}} {{date:}}"
    assert {issue.code for issue in rendered.issues} == {"unknown_token", "empty_format"}


def test_second_cursor_marker_remains_literal_and_is_reported() -> None:
    rendered = render_template("a{{cursor}}b{{cursor}}c", now=NOW)
    assert rendered.text == "ab{{cursor}}c"
    assert rendered.cursor_from_end == len("b{{cursor}}c")
    assert rendered.issues[0].code == "multiple_cursor"


def test_clipboard_provider_failure_is_safe() -> None:
    def broken_clipboard() -> str:
        raise RuntimeError("busy")

    rendered = render_template("x{{clipboard}}y", clipboard_provider=broken_clipboard, now=NOW)
    assert rendered.text == "xy"
    assert rendered.issues[0].code == "clipboard_error"


def test_inspection_does_not_require_live_clipboard() -> None:
    assert inspect_template("{{clipboard}}") == ()
