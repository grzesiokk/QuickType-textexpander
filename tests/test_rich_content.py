from __future__ import annotations

from PySide6.QtGui import QImage

from quicktype.models import (
    Snippet,
    SnippetBundle,
    SnippetContentFormat,
    TriggerMode,
)
from quicktype.rich_content import (
    ASSET_SCHEME,
    RichContentError,
    create_image_asset,
    html_to_plain,
    render_bundle,
    sanitize_html,
)


def test_safe_html_removes_executable_and_remote_content() -> None:
    source = (
        '<script>alert(1)</script><p onclick="bad()" style="color: red; '
        'background-image: url(https://bad)">Hello</p>'
        '<img src="https://example.com/tracker.png" alt="removed">'
    )

    sanitized = sanitize_html(source)

    assert "script" not in sanitized
    assert "onclick" not in sanitized
    assert "background-image" not in sanitized
    assert "https://example.com/tracker.png" not in sanitized
    assert "Hello" in sanitized
    assert "removed" in html_to_plain(sanitized)


def test_rich_renderer_publishes_plain_html_rtf_and_embedded_image() -> None:
    image = QImage(4, 2, QImage.Format.Format_ARGB32)
    image.fill(0xFF336699)
    asset = create_image_asset(image, original_name="logo.png")
    source = (
        f'<p><b>Hello</b> {{{{clipboard}}}}'
        f'<img src="{ASSET_SCHEME}{asset.asset_id}" alt="logo"></p>'
    )
    snippet = Snippet(
        None,
        ";rich",
        "Hello {{clipboard}}[Image: logo]",
        TriggerMode.IMMEDIATE,
        content_format=SnippetContentFormat.RICH,
        rich_html=source,
    )

    rendered = render_bundle(
        SnippetBundle(snippet, (asset,)),
        clipboard_text="<unsafe>",
    )

    assert rendered.plain_text == "Hello <unsafe>[Image: logo]"
    assert "<b>Hello</b> &lt;unsafe&gt;" in rendered.html
    assert "data:image/png;base64," in rendered.html
    assert rendered.rtf.startswith(b"{\\rtf1")
    assert b"\\pict\\pngblip" in rendered.rtf


def test_nested_rich_snippet_keeps_formatting() -> None:
    nested = SnippetBundle(
        Snippet(
            None,
            "nested",
            "Nested",
            TriggerMode.IMMEDIATE,
            content_format=SnippetContentFormat.RICH,
            rich_html="<p><b>Nested</b></p>",
        )
    )
    parent = SnippetBundle(
        Snippet(
            None,
            "parent",
            "Before {{snippet:nested}} After",
            TriggerMode.IMMEDIATE,
            content_format=SnippetContentFormat.RICH,
            rich_html="<p>Before {{snippet:nested}} After</p>",
        )
    )

    rendered = render_bundle(
        parent,
        snippet_provider={"nested": nested}.get,
    )

    assert rendered.plain_text == "Before Nested After"
    assert "<b>Nested</b>" in rendered.html


def test_rich_cursor_validation_includes_nested_images() -> None:
    image = QImage(2, 2, QImage.Format.Format_ARGB32)
    image.fill(0xFF000000)
    asset = create_image_asset(image)
    nested = SnippetBundle(
        Snippet(
            None,
            "nested",
            "[Image: image]",
            TriggerMode.IMMEDIATE,
            content_format=SnippetContentFormat.RICH,
            rich_html=(
                f'<p><img src="{ASSET_SCHEME}{asset.asset_id}" '
                'alt="image"></p>'
            ),
        ),
        (asset,),
    )
    parent = SnippetBundle(
        Snippet(
            None,
            "parent",
            "{{cursor}}{{snippet:nested}}",
            TriggerMode.IMMEDIATE,
            content_format=SnippetContentFormat.RICH,
            rich_html="<p>{{cursor}}{{snippet:nested}}</p>",
        )
    )

    try:
        render_bundle(parent, snippet_provider={"nested": nested}.get)
    except RichContentError as error:
        assert "images cannot appear after" in str(error)
    else:
        raise AssertionError("Expected nested cursor/image validation to fail")


def test_rich_sanitizer_rejects_a_smart_element_split_by_formatting() -> None:
    try:
        sanitize_html("<p>{{clip<b>board</b>}}</p>")
    except RichContentError as error:
        assert "split by formatting" in str(error)
    else:
        raise AssertionError("Expected a split Smart Element to be rejected")


def test_rich_sanitizer_rejects_a_missing_local_image() -> None:
    try:
        sanitize_html(
            '<p><img src="quicktype-asset://'
            '11111111-1111-1111-1111-111111111111"></p>'
        )
    except RichContentError as error:
        assert "missing image" in str(error)
    else:
        raise AssertionError("Expected a missing image to be rejected")


def test_rtf_preserves_inline_foreground_and_background_colors() -> None:
    bundle = SnippetBundle(
        Snippet(
            None,
            "color",
            "Color",
            TriggerMode.IMMEDIATE,
            content_format=SnippetContentFormat.RICH,
            rich_html=(
                '<p><span style="color: #123456; '
                'background-color: #fedcba; font-family: Consolas">'
                "Color</span></p>"
            ),
        )
    )

    rendered = render_bundle(bundle)

    assert b"\\red18\\green52\\blue86;" in rendered.rtf
    assert b"\\red254\\green220\\blue186;" in rendered.rtf
    assert b"\\cf1 " in rendered.rtf
    assert b"\\highlight2 " in rendered.rtf
    assert b"{\\f4 Consolas;}" in rendered.rtf
    assert b"\\f4 " in rendered.rtf


def test_rich_cursor_cannot_precede_an_image() -> None:
    image = QImage(2, 2, QImage.Format.Format_ARGB32)
    image.fill(0xFF000000)
    asset = create_image_asset(image)
    snippet = Snippet(
        None,
        "cursor",
        "Text {{cursor}}[Image: image]",
        TriggerMode.IMMEDIATE,
        content_format=SnippetContentFormat.RICH,
        rich_html=(
            f'<p>Text {{{{cursor}}}}'
            f'<img src="{ASSET_SCHEME}{asset.asset_id}" alt="image"></p>'
        ),
    )

    try:
        render_bundle(SnippetBundle(snippet, (asset,)))
    except RichContentError as error:
        assert "images cannot appear after" in str(error)
    else:
        raise AssertionError("Expected cursor/image validation to fail")
