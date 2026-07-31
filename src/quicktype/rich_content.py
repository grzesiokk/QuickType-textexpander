from __future__ import annotations

import base64
import hashlib
import html
import re
import uuid
from html.parser import HTMLParser
from typing import Callable, Mapping
from urllib.parse import urlparse

from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QColor, QImage

from .models import (
    RenderedContent,
    SnippetAsset,
    SnippetBundle,
    SnippetContentFormat,
)
from .template_engine import TOKEN_RE, render_template

ASSET_SCHEME = "quicktype-asset://"
DATA_IMAGE_RE = re.compile(
    r"""(?P<prefix><img\b[^>]*\bsrc\s*=\s*["'])
        data:(?P<mime>image/(?:png|jpeg));base64,(?P<data>[A-Za-z0-9+/=\s]+)
        (?P<suffix>["'][^>]*>)""",
    re.IGNORECASE | re.VERBOSE,
)
ASSET_URL_RE = re.compile(r"quicktype-asset://([0-9a-fA-F-]{36})")
SAFE_TAGS = {
    "p",
    "div",
    "br",
    "span",
    "strong",
    "b",
    "em",
    "i",
    "u",
    "s",
    "strike",
    "ul",
    "ol",
    "li",
    "a",
    "img",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "blockquote",
}
VOID_TAGS = {"br", "img"}
BLOCKED_TAGS = {"script", "style", "iframe", "object", "embed", "form", "input", "button"}
SAFE_STYLE_PROPERTIES = {
    "font-family",
    "font-size",
    "font-style",
    "font-weight",
    "text-decoration",
    "color",
    "background-color",
    "text-align",
    "margin-top",
    "margin-bottom",
    "margin-left",
    "margin-right",
    "text-indent",
    "white-space",
    "-qt-block-indent",
}
SAFE_LINK_SCHEMES = {"http", "https", "mailto"}
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_DIMENSION = 16_384


class RichContentError(ValueError):
    pass


def create_image_asset(
    image: QImage,
    *,
    original_name: str = "image.png",
    prefer_jpeg: bool = False,
) -> SnippetAsset:
    if image.isNull():
        raise RichContentError("The selected image could not be decoded.")
    if (
        image.width() > MAX_IMAGE_DIMENSION
        or image.height() > MAX_IMAGE_DIMENSION
    ):
        raise RichContentError("An image exceeds the 16384-pixel dimension limit.")
    image_format = b"JPEG" if prefer_jpeg and not image.hasAlphaChannel() else b"PNG"
    mime_type = "image/jpeg" if image_format == b"JPEG" else "image/png"
    suffix = ".jpg" if mime_type == "image/jpeg" else ".png"
    normalized_name = _safe_image_name(original_name, suffix)
    data = QByteArray()
    buffer = QBuffer(data)
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
        raise RichContentError("The image could not be prepared.")
    quality = 92 if image_format == b"JPEG" else -1
    try:
        if not image.save(  # type: ignore[call-overload]
            buffer,
            image_format.decode("ascii"),
            quality,
        ):
            raise RichContentError("The image could not be encoded.")
    finally:
        buffer.close()
    encoded = bytes(data.data())
    if len(encoded) > MAX_IMAGE_BYTES:
        raise RichContentError("An image exceeds the 10 MiB limit.")
    return SnippetAsset(
        asset_id=str(uuid.uuid4()),
        mime_type=mime_type,
        data=encoded,
        original_name=normalized_name,
        width=image.width(),
        height=image.height(),
        sha256=hashlib.sha256(encoded).hexdigest(),
    )


def import_data_images(
    source: str,
    assets: tuple[SnippetAsset, ...] = (),
) -> tuple[str, tuple[SnippetAsset, ...]]:
    collected = list(assets)
    by_hash = {asset.sha256: asset for asset in collected}

    def replace(match: re.Match[str]) -> str:
        try:
            raw = base64.b64decode(match.group("data"), validate=True)
        except ValueError as error:
            raise RichContentError("An embedded image contains invalid base64 data.") from error
        image = QImage.fromData(raw)
        asset = create_image_asset(
            image,
            original_name="embedded.png",
            prefer_jpeg=match.group("mime").casefold() == "image/jpeg",
        )
        existing = by_hash.get(asset.sha256)
        if existing is None:
            collected.append(asset)
            by_hash[asset.sha256] = asset
        else:
            asset = existing
        return f'{match.group("prefix")}{ASSET_SCHEME}{asset.asset_id}{match.group("suffix")}'

    return DATA_IMAGE_RE.sub(replace, source), tuple(collected)


def sanitize_html(source: str, assets: tuple[SnippetAsset, ...] = ()) -> str:
    asset_ids = {asset.asset_id for asset in assets}
    missing_assets = set(ASSET_URL_RE.findall(source)) - asset_ids
    if missing_assets:
        raise RichContentError("Rich HTML references a missing image.")
    parser = _SafeHtmlParser(asset_ids)
    try:
        parser.feed(source)
        parser.close()
    except (ValueError, AssertionError) as error:
        raise RichContentError(f"Invalid HTML: {error}") from error
    fragment = parser.output()
    if not fragment.strip():
        return "<p></p>"
    _validate_smart_element_boundaries(fragment)
    return fragment


def html_to_plain(source: str) -> str:
    parser = _PlainTextParser()
    parser.feed(source)
    parser.close()
    return parser.output()


def render_bundle(
    bundle: SnippetBundle,
    *,
    clipboard_text: str = "",
    values: Mapping[str, str] | None = None,
    match_groups: Mapping[str, str] | None = None,
    snippet_provider: Callable[[str], SnippetBundle | None] | None = None,
) -> RenderedContent:
    snippet = bundle.snippet
    plain_provider: Callable[[str], str | None] | None = None
    if snippet_provider is not None:
        def plain_provider(abbreviation: str) -> str | None:
            return _plain_template(snippet_provider(abbreviation))
    rendered_plain = render_template(
        snippet.expansion,
        clipboard_text=clipboard_text,
        values=values,
        match_groups=match_groups,
        snippet_provider=plain_provider,
    )
    if rendered_plain.issues:
        raise RichContentError(rendered_plain.issues[0].message)
    if snippet.content_format == SnippetContentFormat.PLAIN:
        return RenderedContent(
            plain_text=rendered_plain.text,
            cursor_from_end=rendered_plain.cursor_from_end,
            cursor_present=rendered_plain.cursor_present,
        )

    canonical = sanitize_html(snippet.rich_html, bundle.assets)
    if _has_image_after_cursor(bundle, snippet_provider):
        raise RichContentError(
            "In a rich snippet, images cannot appear after the cursor marker."
        )
    parser = _RenderedHtmlParser(
        clipboard_text=clipboard_text,
        values=values or {},
        match_groups=match_groups or {},
        plain_provider=plain_provider,
        bundle_provider=snippet_provider,
    )
    parser.feed(canonical)
    parser.close()
    rendered_html = parser.output()
    clipboard_html = _embed_asset_data(rendered_html, bundle.assets)
    rtf = html_to_rtf(rendered_html, bundle.assets)
    return RenderedContent(
        plain_text=rendered_plain.text,
        html=clipboard_html,
        rtf=rtf,
        cursor_from_end=rendered_plain.cursor_from_end,
        cursor_present=rendered_plain.cursor_present,
    )


def html_to_rtf(source: str, assets: tuple[SnippetAsset, ...]) -> bytes:
    parser = _RtfParser({asset.asset_id: asset for asset in assets})
    parser.feed(source)
    parser.close()
    font_table = "".join(
        rf"{{\f{index} {_rtf_text(name)};}}"
        for index, name in enumerate(parser.fonts)
    )
    color_table = "".join(
        rf"\red{red}\green{green}\blue{blue};"
        for red, green, blue in parser.colors
    )
    document = (
        r"{\rtf1\ansi\deff0"
        r"{\fonttbl"
        + font_table
        + "}"
        r"{\colortbl;"
        + color_table
        + "}"
        r"\viewkind4\uc1 "
        + parser.output()
        + "}"
    )
    return document.encode("ascii", errors="strict")


def _plain_template(bundle: SnippetBundle | None) -> str | None:
    return bundle.snippet.expansion if bundle is not None else None


def _safe_image_name(name: str, suffix: str) -> str:
    candidate = name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    candidate = "".join(character for character in candidate if ord(character) >= 32)
    if not candidate:
        candidate = f"image{suffix}"
    if len(candidate) > 255:
        stem = candidate.rsplit(".", 1)[0][: 255 - len(suffix)]
        candidate = f"{stem}{suffix}"
    return candidate


def _sanitize_style(value: str) -> str:
    declarations: list[str] = []
    for raw in value.split(";"):
        if ":" not in raw:
            continue
        name, setting = raw.split(":", 1)
        name = name.strip().casefold()
        setting = setting.strip()
        lowered = setting.casefold()
        if (
            name not in SAFE_STYLE_PROPERTIES
            or not setting
            or any(token in lowered for token in ("url(", "expression", "javascript:", "{", "}"))
        ):
            continue
        declarations.append(f"{name}: {setting}")
    return "; ".join(declarations)


def _safe_href(value: str) -> str:
    candidate = value.strip()
    parsed = urlparse(candidate)
    if parsed.scheme.casefold() in SAFE_LINK_SCHEMES:
        return candidate
    return ""


class _SafeHtmlParser(HTMLParser):
    def __init__(self, asset_ids: set[str]) -> None:
        super().__init__(convert_charrefs=True)
        self.asset_ids = asset_ids
        self.parts: list[str] = []
        self.blocked_depth = 0
        self.open_tags: list[str] = []
        self.table_cell = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if tag in BLOCKED_TAGS:
            self.blocked_depth += 1
            return
        if self.blocked_depth:
            return
        if tag == "tr":
            self.parts.append("<p>")
            self.table_cell = 0
            return
        if tag in {"td", "th"}:
            if self.table_cell:
                self.parts.append("&#9;")
            self.table_cell += 1
            return
        if tag not in SAFE_TAGS:
            return
        cleaned: list[tuple[str, str]] = []
        raw_attrs = {name.casefold(): value or "" for name, value in attrs}
        style = _sanitize_style(raw_attrs.get("style", ""))
        if style and tag not in {"br", "img"}:
            cleaned.append(("style", style))
        if tag == "a":
            href = _safe_href(raw_attrs.get("href", ""))
            if href:
                cleaned.append(("href", href))
        elif tag == "img":
            source = raw_attrs.get("src", "")
            match = ASSET_URL_RE.fullmatch(source)
            if match is None or match.group(1) not in self.asset_ids:
                alt = raw_attrs.get("alt", "").strip()
                if alt:
                    self.parts.append(html.escape(alt))
                return
            cleaned.append(("src", source))
            alt = raw_attrs.get("alt", "")[:500]
            if alt:
                cleaned.append(("alt", alt))
            for dimension in ("width", "height"):
                raw_value = raw_attrs.get(dimension, "")
                if raw_value.isdigit() and 1 <= int(raw_value) <= 16384:
                    cleaned.append((dimension, raw_value))
        rendered_attrs = "".join(
            f' {name}="{html.escape(value, quote=True)}"' for name, value in cleaned
        )
        self.parts.append(f"<{tag}{rendered_attrs}>")
        if tag not in VOID_TAGS:
            self.open_tags.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag in BLOCKED_TAGS:
            if self.blocked_depth:
                self.blocked_depth -= 1
            return
        if self.blocked_depth:
            return
        if tag == "tr":
            self.parts.append("</p>")
            return
        if tag in {"td", "th"} or tag not in SAFE_TAGS or tag in VOID_TAGS:
            return
        if tag not in self.open_tags:
            return
        while self.open_tags:
            current = self.open_tags.pop()
            self.parts.append(f"</{current}>")
            if current == tag:
                break

    def handle_data(self, data: str) -> None:
        if not self.blocked_depth:
            self.parts.append(html.escape(data))

    def output(self) -> str:
        while self.open_tags:
            self.parts.append(f"</{self.open_tags.pop()}>")
        return "".join(self.parts)


class _PlainTextParser(HTMLParser):
    BLOCK_TAGS = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.suppressed = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if tag in {"head", "style", "script"}:
            self.suppressed += 1
            return
        if self.suppressed:
            return
        if tag == "br":
            self.parts.append("\n")
        elif tag == "li":
            self._newline()
            self.parts.append("• ")
        elif tag == "img":
            alt = dict(attrs).get("alt") or ""
            if alt:
                self.parts.append(f"[Image: {alt}]")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag in {"head", "style", "script"}:
            if self.suppressed:
                self.suppressed -= 1
            return
        if not self.suppressed and tag in self.BLOCK_TAGS:
            self._newline()

    def handle_data(self, data: str) -> None:
        if not self.suppressed:
            self.parts.append(data)

    def _newline(self) -> None:
        if self.parts and not self.parts[-1].endswith("\n"):
            self.parts.append("\n")

    def output(self) -> str:
        value = "".join(self.parts).replace("\r", "")
        value = re.sub(r"[ \t]+\n", "\n", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        return value.strip("\n")


class _RenderedHtmlParser(HTMLParser):
    def __init__(
        self,
        *,
        clipboard_text: str,
        values: Mapping[str, str],
        match_groups: Mapping[str, str],
        plain_provider: Callable[[str], str | None] | None,
        bundle_provider: Callable[[str], SnippetBundle | None] | None,
    ) -> None:
        super().__init__(convert_charrefs=True)
        self.clipboard_text = clipboard_text
        self.values = values
        self.match_groups = match_groups
        self.plain_provider = plain_provider
        self.bundle_provider = bundle_provider
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        rendered = "".join(
            f' {name}="{html.escape(value or "", quote=True)}"' for name, value in attrs
        )
        self.parts.append(f"<{tag}{rendered}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag not in VOID_TAGS:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        position = 0
        for match in TOKEN_RE.finditer(data):
            self.parts.append(html.escape(data[position : match.start()]))
            token = match.group(1).strip()
            nested = None
            if token.startswith("snippet:") and self.bundle_provider is not None:
                nested = self.bundle_provider(token[8:].strip())
            if nested is not None and nested.snippet.content_format == SnippetContentFormat.RICH:
                rendered_nested = render_bundle(
                    nested,
                    clipboard_text=self.clipboard_text,
                    values=self.values,
                    match_groups=self.match_groups,
                    snippet_provider=self.bundle_provider,
                )
                self.parts.append(
                    rendered_nested.html
                    or html.escape(rendered_nested.plain_text).replace("\n", "<br>")
                )
            else:
                rendered = render_template(
                    match.group(0),
                    clipboard_text=self.clipboard_text,
                    values=self.values,
                    match_groups=self.match_groups,
                    snippet_provider=self.plain_provider,
                )
                if rendered.issues:
                    raise RichContentError(rendered.issues[0].message)
                self.parts.append(
                    html.escape(rendered.text).replace("\n", "<br>")
                )
            position = match.end()
        self.parts.append(html.escape(data[position:]))

    def output(self) -> str:
        return "".join(self.parts)


def _embed_asset_data(source: str, assets: tuple[SnippetAsset, ...]) -> str:
    by_id = {asset.asset_id: asset for asset in assets}

    def replace(match: re.Match[str]) -> str:
        asset = by_id.get(match.group(1))
        if asset is None:
            raise RichContentError("Rich HTML references a missing image.")
        encoded = base64.b64encode(asset.data).decode("ascii")
        return f"data:{asset.mime_type};base64,{encoded}"

    return ASSET_URL_RE.sub(replace, source)


def _validate_smart_element_boundaries(source: str) -> None:
    collector = _SmartElementCollector()
    collector.feed(source)
    collector.close()
    plain_tokens = [match.group(0) for match in TOKEN_RE.finditer(html_to_plain(source))]
    node_tokens = [
        match.group(0)
        for value in collector.text_nodes
        for match in TOKEN_RE.finditer(value)
    ]
    if plain_tokens != node_tokens:
        raise RichContentError(
            "A Smart Element is split by formatting and must be inserted again."
        )


class _SmartElementCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_nodes: list[str] = []

    def handle_data(self, data: str) -> None:
        self.text_nodes.append(data)


def _has_image_after_cursor(
    bundle: SnippetBundle,
    provider: Callable[[str], SnippetBundle | None] | None,
) -> bool:
    markers = _bundle_markers(bundle, provider, ())
    cursor_seen = False
    for marker in markers:
        if marker == "cursor":
            cursor_seen = True
        elif marker == "image" and cursor_seen:
            return True
    return False


def _bundle_markers(
    bundle: SnippetBundle,
    provider: Callable[[str], SnippetBundle | None] | None,
    stack: tuple[str, ...],
) -> list[str]:
    key = bundle.snippet.abbreviation
    if key in stack or len(stack) >= 10:
        return []
    next_stack = (*stack, key)
    if bundle.snippet.content_format == SnippetContentFormat.PLAIN:
        return _plain_markers(bundle.snippet.expansion, provider, next_stack)
    canonical = sanitize_html(bundle.snippet.rich_html, bundle.assets)
    parser = _RichMarkerParser(provider, next_stack)
    parser.feed(canonical)
    parser.close()
    return parser.markers


def _plain_markers(
    template: str,
    provider: Callable[[str], SnippetBundle | None] | None,
    stack: tuple[str, ...],
) -> list[str]:
    markers: list[str] = []
    for match in TOKEN_RE.finditer(template):
        token = match.group(1).strip()
        if token == "cursor":
            markers.append("cursor")
        elif token.startswith("snippet:") and provider is not None:
            nested = provider(token[8:].strip())
            if (
                nested is not None
                and nested.snippet.abbreviation not in stack
                and len(stack) < 10
            ):
                markers.extend(
                    _plain_markers(
                        nested.snippet.expansion,
                        provider,
                        (*stack, nested.snippet.abbreviation),
                    )
                )
    return markers


class _RichMarkerParser(HTMLParser):
    def __init__(
        self,
        provider: Callable[[str], SnippetBundle | None] | None,
        stack: tuple[str, ...],
    ) -> None:
        super().__init__(convert_charrefs=True)
        self.provider = provider
        self.stack = stack
        self.markers: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() == "img":
            self.markers.append("image")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_data(self, data: str) -> None:
        for match in TOKEN_RE.finditer(data):
            token = match.group(1).strip()
            if token == "cursor":
                self.markers.append("cursor")
            elif token.startswith("snippet:") and self.provider is not None:
                nested = self.provider(token[8:].strip())
                if nested is None:
                    continue
                if nested.snippet.content_format == SnippetContentFormat.RICH:
                    self.markers.extend(
                        _bundle_markers(nested, self.provider, self.stack)
                    )
                else:
                    if (
                        nested.snippet.abbreviation not in self.stack
                        and len(self.stack) < 10
                    ):
                        self.markers.extend(
                            _plain_markers(
                                nested.snippet.expansion,
                                self.provider,
                                (*self.stack, nested.snippet.abbreviation),
                            )
                        )


def _rtf_text(value: str) -> str:
    parts: list[str] = []
    for character in value:
        if character in r"\{}":
            parts.append("\\" + character)
        elif character == "\n":
            parts.append(r"\line ")
        else:
            codepoint = ord(character)
            if 32 <= codepoint <= 126:
                parts.append(character)
            else:
                encoded = character.encode("utf-16-le", errors="surrogatepass")
                for offset in range(0, len(encoded), 2):
                    unit = int.from_bytes(encoded[offset : offset + 2], "little", signed=False)
                    signed = unit if unit < 32768 else unit - 65536
                    parts.append(rf"\u{signed}?")
    return "".join(parts)


class _RtfParser(HTMLParser):
    def __init__(self, assets: dict[str, SnippetAsset]) -> None:
        super().__init__(convert_charrefs=True)
        self.assets = assets
        self.parts: list[str] = []
        self.suppressed = 0
        self.list_stack: list[tuple[str, int]] = []
        self.groups: list[str] = []
        self.colors: list[tuple[int, int, int]] = []
        self.fonts = ["Calibri", "Arial", "Times New Roman", "Courier New"]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        values = {name.casefold(): value or "" for name, value in attrs}
        if tag in {"head", "style", "script"}:
            self.suppressed += 1
            return
        if self.suppressed:
            return
        if tag in {"p", "div", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6"}:
            align = _style_value(values.get("style", ""), "text-align")
            alignment = {"center": r"\qc", "right": r"\qr", "justify": r"\qj"}.get(
                align, r"\ql"
            )
            size = {
                "h1": 36,
                "h2": 30,
                "h3": 26,
                "h4": 24,
                "h5": 22,
                "h6": 20,
            }.get(tag, 22)
            self.parts.append(rf"\pard{alignment}\fs{size} ")
        elif tag == "br":
            self.parts.append(r"\line ")
        elif tag in {"b", "strong"}:
            self._open_group(tag, r"{\b ")
        elif tag in {"i", "em"}:
            self._open_group(tag, r"{\i ")
        elif tag == "u":
            self._open_group(tag, r"{\ul ")
        elif tag in {"s", "strike"}:
            self._open_group(tag, r"{\strike ")
        elif tag == "span":
            controls = _rtf_style_controls(
                values.get("style", ""),
                self._color_index,
                self._font_index,
            )
            self._open_group(tag, "{" + controls)
        elif tag in {"ul", "ol"}:
            self.list_stack.append((tag, 0))
        elif tag == "li":
            if self.list_stack:
                kind, count = self.list_stack[-1]
                count += 1
                self.list_stack[-1] = (kind, count)
                marker = r"\bullet" if kind == "ul" else _rtf_text(f"{count}.")
                self.parts.append(rf"\pard\fi-360\li720 {marker}\tab ")
        elif tag == "a":
            href = _safe_href(values.get("href", ""))
            if href:
                escaped_href = _rtf_text(href).replace('"', r"\"")
                self._open_group(
                    tag,
                    r'{\field{\*\fldinst HYPERLINK "' + escaped_href + r'"}{\fldrslt ',
                )
        elif tag == "img":
            self._image(values.get("src", ""), values)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag in {"head", "style", "script"}:
            if self.suppressed:
                self.suppressed -= 1
            return
        if self.suppressed:
            return
        if tag in {"p", "div", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "li"}:
            self.parts.append(r"\par ")
        elif tag in {"ul", "ol"}:
            if self.list_stack:
                self.list_stack.pop()
        elif tag in {"b", "strong", "i", "em", "u", "s", "strike", "span", "a"}:
            self._close_group(tag)

    def handle_data(self, data: str) -> None:
        if not self.suppressed:
            self.parts.append(_rtf_text(data))

    def _open_group(self, tag: str, prefix: str) -> None:
        self.parts.append(prefix)
        self.groups.append(tag)

    def _color_index(self, value: str) -> int | None:
        color = QColor(value)
        if not color.isValid() or color.alpha() == 0:
            return None
        rgb = (color.red(), color.green(), color.blue())
        if rgb not in self.colors:
            self.colors.append(rgb)
        return self.colors.index(rgb) + 1

    def _font_index(self, value: str) -> int | None:
        family = value.split(",", 1)[0].strip().strip("\"'")
        if (
            not family
            or len(family) > 100
            or any(ord(character) < 32 for character in family)
        ):
            return None
        folded = family.casefold()
        for index, current in enumerate(self.fonts):
            if current.casefold() == folded:
                return index
        self.fonts.append(family)
        return len(self.fonts) - 1

    def _close_group(self, tag: str) -> None:
        if tag not in self.groups:
            return
        while self.groups:
            current = self.groups.pop()
            self.parts.append("}}" if current == "a" else "}")
            if current == tag:
                break

    def _image(self, source: str, attrs: Mapping[str, str]) -> None:
        match = ASSET_URL_RE.fullmatch(source)
        asset = self.assets.get(match.group(1)) if match is not None else None
        if asset is None and source.startswith("data:image/"):
            data_match = re.fullmatch(
                r"data:(image/(?:png|jpeg));base64,([A-Za-z0-9+/=]+)",
                source,
                flags=re.IGNORECASE,
            )
            if data_match is not None:
                try:
                    data = base64.b64decode(data_match.group(2), validate=True)
                except ValueError:
                    return
                image = QImage.fromData(data)
                if not image.isNull():
                    asset = SnippetAsset(
                        asset_id=str(uuid.uuid4()),
                        mime_type=data_match.group(1).casefold(),
                        data=data,
                        original_name="nested.png",
                        width=image.width(),
                        height=image.height(),
                        sha256=hashlib.sha256(data).hexdigest(),
                    )
        if asset is None:
            return
        width = int(attrs.get("width", asset.width)) if attrs.get("width", "").isdigit() else asset.width
        height = (
            int(attrs.get("height", asset.height))
            if attrs.get("height", "").isdigit()
            else round(asset.height * width / asset.width)
        )
        kind = r"\pngblip" if asset.mime_type == "image/png" else r"\jpegblip"
        goal_width = max(1, width) * 15
        goal_height = max(1, height) * 15
        self.parts.append(
            rf"{{\pict{kind}\picw{asset.width}\pich{asset.height}"
            rf"\picwgoal{goal_width}\pichgoal{goal_height} "
            + asset.data.hex().upper()
            + "}"
        )

    def output(self) -> str:
        while self.groups:
            self.parts.append("}}" if self.groups.pop() == "a" else "}")
        return "".join(self.parts)


def _style_value(
    style: str,
    name: str,
    *,
    casefold: bool = True,
) -> str:
    for declaration in style.split(";"):
        if ":" not in declaration:
            continue
        key, value = declaration.split(":", 1)
        if key.strip().casefold() == name:
            cleaned = value.strip()
            return cleaned.casefold() if casefold else cleaned
    return ""


def _rtf_style_controls(
    style: str,
    color_index: Callable[[str], int | None],
    font_index: Callable[[str], int | None],
) -> str:
    controls: list[str] = []
    weight = _style_value(style, "font-weight")
    if weight in {"bold", "600", "700", "800", "900"}:
        controls.append(r"\b ")
    if _style_value(style, "font-style") == "italic":
        controls.append(r"\i ")
    decoration = _style_value(style, "text-decoration")
    if "underline" in decoration:
        controls.append(r"\ul ")
    if "line-through" in decoration:
        controls.append(r"\strike ")
    size = _style_value(style, "font-size")
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(pt|px)", size)
    if match:
        points = float(match.group(1))
        if match.group(2) == "px":
            points *= 0.75
        controls.append(rf"\fs{max(2, round(points * 2))} ")
    family = font_index(
        _style_value(style, "font-family", casefold=False)
    )
    if family is not None:
        controls.append(rf"\f{family} ")
    foreground = color_index(_style_value(style, "color"))
    if foreground is not None:
        controls.append(rf"\cf{foreground} ")
    background = color_index(_style_value(style, "background-color"))
    if background is not None:
        controls.append(rf"\highlight{background} ")
    return "".join(controls)
