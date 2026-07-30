from datetime import datetime

import pytest

from quicktype.template_engine import (
    FormFieldKind,
    calculate_expression,
    collect_form_fields,
    inspect_template,
    render_template,
)

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


def test_text_transforms_render_unicode_sources_and_default_values() -> None:
    rendered = render_template(
        "{{upper:var:name}}|{{lower:clipboard}}|{{title:match:1}}|"
        "{{trim:var:company}}|{{default:var:missing|Brak\\|danych}}",
        values={"name": "Zażółć", "company": "  ACME  "},
        clipboard_text="MIXED TEXT",
        match_groups={"1": "jan kowalski"},
        now=NOW,
    )

    assert rendered.text == "ZAŻÓŁĆ|mixed text|Jan Kowalski|ACME|Brak|danych"
    assert not rendered.issues


def test_text_transform_missing_source_is_an_issue_but_default_is_safe() -> None:
    missing = render_template("{{upper:var:missing}}", now=NOW)
    fallback = render_template("{{default:match:nope|fallback}}", now=NOW)
    invalid = render_template("{{upper:literal}}", now=NOW)

    assert missing.text == "{{upper:var:missing}}"
    assert missing.issues[0].code == "missing_variable"
    assert fallback.text == "fallback"
    assert not fallback.issues
    assert invalid.text == "{{upper:literal}}"
    assert invalid.issues[0].code == "invalid_transform"


def test_collects_and_renders_form_fields_and_variables() -> None:
    template = (
        "{{input:name|Klient|Anna}} / "
        "{{choice:plan|Plan|Basic|Pro}} / "
        "{{check:vip|VIP|tak|nie}} / {{var:name}}"
    )

    fields, issues = collect_form_fields(template)
    rendered = render_template(
        template,
        values={"name": "Jan", "plan": "Pro", "vip": "nie"},
        now=NOW,
    )

    assert not issues
    assert [field.kind for field in fields] == [
        FormFieldKind.INPUT,
        FormFieldKind.CHOICE,
        FormFieldKind.CHECK,
    ]
    assert rendered.text == "Jan / Pro / nie / Jan"
    assert not rendered.issues


def test_field_parts_support_escaped_pipes_and_backslashes() -> None:
    fields, issues = collect_form_fields(r"{{input:path|Ścieżka|C:\\Temp\|Archiwum}}")

    assert not issues
    assert fields[0].default == r"C:\Temp|Archiwum"


def test_safe_calculation_uses_decimal_variables() -> None:
    assert calculate_expression("(quantity * price) + 0.2", {"quantity": "3", "price": "1,10"}) == "3.5"
    assert render_template(
        "{{calc:quantity * price}}",
        values={"quantity": "3", "price": "2.5"},
        now=NOW,
    ).text == "7.5"


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('whoami')",
        "open('secret')",
        "2 ** 999",
        "1 / 0",
    ],
)
def test_calculation_rejects_unsafe_or_unbounded_expressions(expression: str) -> None:
    with pytest.raises(ValueError):
        calculate_expression(expression)


def test_snippet_composition_and_cycle_detection() -> None:
    snippets = {
        "address": "Warszawa{{cursor}}",
        "signature": "Pozdrawiam\n{{snippet:address}}",
        "cycle-a": "{{snippet:cycle-b}}",
        "cycle-b": "{{snippet:cycle-a}}",
    }

    rendered = render_template(
        "Start\n{{snippet:signature}}",
        snippet_provider=snippets.get,
        now=NOW,
    )
    cycle = render_template(
        "{{snippet:cycle-a}}",
        snippet_provider=snippets.get,
        now=NOW,
    )

    assert rendered.text == "Start\nPozdrawiam\nWarszawa"
    assert rendered.cursor_present
    assert rendered.cursor_from_end == 0
    assert not rendered.issues
    assert any(issue.code == "snippet_cycle" for issue in cycle.issues)


def test_match_groups_and_comments_are_rendered() -> None:
    rendered = render_template(
        "Nr {{match:number}}{{-- internal note --}}",
        match_groups={"number": "123"},
        now=NOW,
    )

    assert rendered.text == "Nr 123"
    assert not rendered.issues
