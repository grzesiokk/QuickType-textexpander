from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import datetime
from decimal import Decimal, DivisionByZero, InvalidOperation, localcontext
from enum import StrEnum
from typing import Callable, Mapping

TOKEN_RE = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)
FIELD_ID_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,63}\Z")
MAX_CALC_LENGTH = 200
MAX_SNIPPET_DEPTH = 10
MAX_RESULT_MAGNITUDE = Decimal("1e100")


class FormFieldKind(StrEnum):
    INPUT = "input"
    CHOICE = "choice"
    CHECK = "check"


@dataclass(frozen=True, slots=True)
class FormField:
    identifier: str
    label: str
    kind: FormFieldKind
    default: str = ""
    options: tuple[str, ...] = ()
    checked_text: str = ""
    unchecked_text: str = ""


@dataclass(frozen=True, slots=True)
class TemplateIssue:
    code: str
    token: str
    message: str


@dataclass(frozen=True, slots=True)
class RenderedTemplate:
    text: str
    cursor_from_end: int
    issues: tuple[TemplateIssue, ...]
    cursor_present: bool = False


SnippetProvider = Callable[[str], str | None]


def inspect_template(
    template: str,
    *,
    snippet_provider: SnippetProvider | None = None,
    match_groups: Mapping[str, str] | None = None,
) -> tuple[TemplateIssue, ...]:
    return render_template(
        template,
        clipboard_text="",
        now=datetime(2000, 1, 2, 3, 4, 5),
        snippet_provider=snippet_provider,
        match_groups=match_groups,
    ).issues


def collect_form_fields(
    template: str,
    *,
    snippet_provider: SnippetProvider | None = None,
) -> tuple[tuple[FormField, ...], tuple[TemplateIssue, ...]]:
    fields: dict[str, FormField] = {}
    issues: list[TemplateIssue] = []
    _collect_fields(
        template,
        fields,
        issues,
        snippet_provider=snippet_provider,
        stack=(),
    )
    return tuple(fields.values()), tuple(issues)


def render_template(
    template: str,
    *,
    clipboard_text: str = "",
    now: datetime | None = None,
    clipboard_provider: Callable[[], str] | None = None,
    values: Mapping[str, str] | None = None,
    match_groups: Mapping[str, str] | None = None,
    snippet_provider: SnippetProvider | None = None,
) -> RenderedTemplate:
    prepared_values = dict(values or {})
    fields, _field_issues = collect_form_fields(
        template,
        snippet_provider=snippet_provider,
    )
    for field in fields:
        prepared_values.setdefault(field.identifier, field.default)
    state = _RenderState(
        now=now or datetime.now(),
        clipboard_text=clipboard_text,
        clipboard_provider=clipboard_provider,
        values=prepared_values,
        match_groups=dict(match_groups or {}),
        snippet_provider=snippet_provider,
    )
    _render_into(template, state, stack=())
    text = "".join(state.output)
    cursor_present = bool(state.cursor_positions)
    cursor_from_end = (
        len(text) - state.cursor_positions[0]
        if cursor_present
        else 0
    )
    return RenderedTemplate(
        text=text,
        cursor_from_end=cursor_from_end,
        issues=tuple(state.issues),
        cursor_present=cursor_present,
    )


@dataclass(slots=True)
class _RenderState:
    now: datetime
    clipboard_text: str
    clipboard_provider: Callable[[], str] | None
    values: dict[str, str]
    match_groups: dict[str, str]
    snippet_provider: SnippetProvider | None
    output: list[str] = dataclass_field(default_factory=list)
    output_length: int = 0
    issues: list[TemplateIssue] = dataclass_field(default_factory=list)
    cursor_positions: list[int] = dataclass_field(default_factory=list)

    def append(self, value: str) -> None:
        self.output.append(value)
        self.output_length += len(value)


def _render_into(
    template: str,
    state: _RenderState,
    *,
    stack: tuple[str, ...],
) -> None:
    position = 0
    for match in TOKEN_RE.finditer(template):
        state.append(template[position : match.start()])
        raw_token = match.group(1)
        token = raw_token.strip()
        replacement = _render_token(token, state, stack=stack)
        if replacement is None:
            state.append(match.group(0))
        else:
            state.append(replacement)
        position = match.end()
    state.append(template[position:])


def _render_token(
    token: str,
    state: _RenderState,
    *,
    stack: tuple[str, ...],
) -> str | None:
    if token == "date":
        return state.now.strftime("%d.%m.%Y")
    if token.startswith("date:"):
        return _format_datetime(token, token[5:], state.now, state.issues, "Date")
    if token == "time":
        return state.now.strftime("%H:%M")
    if token.startswith("time:"):
        return _format_datetime(token, token[5:], state.now, state.issues, "Time")
    if token == "clipboard":
        if state.clipboard_provider is not None:
            try:
                return state.clipboard_provider() or ""
            except Exception as error:  # Clipboard access can transiently fail on Windows.
                state.issues.append(TemplateIssue("clipboard_error", token, str(error)))
                return ""
        return state.clipboard_text
    if token == "cursor":
        if state.cursor_positions:
            state.issues.append(
                TemplateIssue(
                    "multiple_cursor",
                    token,
                    "The cursor marker can occur only once after snippets are composed.",
                )
            )
            return None
        state.cursor_positions.append(state.output_length)
        return ""
    if token.startswith(("input:", "choice:", "check:")):
        field, issue = _parse_field(token)
        if issue is not None:
            state.issues.append(issue)
            return None
        assert field is not None
        value = _field_value(field, state.values)
        state.values.setdefault(field.identifier, value)
        return value
    if token.startswith("var:"):
        identifier = token[4:].strip()
        if not FIELD_ID_RE.fullmatch(identifier):
            state.issues.append(
                TemplateIssue("invalid_variable", token, "Invalid variable identifier.")
            )
            return None
        if identifier not in state.values:
            state.issues.append(
                TemplateIssue(
                    "missing_variable",
                    token,
                    f"No value was provided for variable '{identifier}'.",
                )
            )
            return None
        return state.values[identifier]
    if token.startswith("calc:"):
        expression = token[5:].strip()
        try:
            return calculate_expression(expression, state.values)
        except ValueError as error:
            state.issues.append(TemplateIssue("calculation_error", token, str(error)))
            return None
    if token.startswith("calc-match:"):
        identifier = token[11:].strip()
        match_expression = state.match_groups.get(identifier)
        if match_expression is None:
            state.issues.append(
                TemplateIssue(
                    "missing_match",
                    token,
                    f"Regular-expression group '{identifier}' is not available.",
                )
            )
            return None
        try:
            return calculate_expression(match_expression)
        except ValueError as error:
            state.issues.append(TemplateIssue("calculation_error", token, str(error)))
            return None
    if token.startswith("match:"):
        identifier = token[6:].strip()
        if identifier not in state.match_groups:
            state.issues.append(
                TemplateIssue(
                    "missing_match",
                    token,
                    f"Regular-expression group '{identifier}' is not available.",
                )
            )
            return None
        return state.match_groups[identifier]
    if token.startswith("snippet:"):
        abbreviation = token[8:].strip()
        if not abbreviation:
            state.issues.append(
                TemplateIssue("empty_snippet", token, "Snippet abbreviation cannot be empty.")
            )
            return None
        if state.snippet_provider is None:
            state.issues.append(
                TemplateIssue(
                    "snippet_unavailable",
                    token,
                    "Snippet composition is unavailable in this context.",
                )
            )
            return None
        if abbreviation in stack:
            state.issues.append(
                TemplateIssue(
                    "snippet_cycle",
                    token,
                    "Snippet composition contains a cycle.",
                )
            )
            return None
        if len(stack) >= MAX_SNIPPET_DEPTH:
            state.issues.append(
                TemplateIssue(
                    "snippet_depth",
                    token,
                    f"Snippet composition cannot exceed {MAX_SNIPPET_DEPTH} levels.",
                )
            )
            return None
        nested = state.snippet_provider(abbreviation)
        if nested is None:
            state.issues.append(
                TemplateIssue(
                    "missing_snippet",
                    token,
                    f"Snippet '{abbreviation}' is missing or unavailable.",
                )
            )
            return None
        _render_into(nested, state, stack=stack + (abbreviation,))
        return ""
    if token.startswith("--"):
        return ""

    state.issues.append(TemplateIssue("unknown_token", token, f"Unknown variable: {token}"))
    return None


def _format_datetime(
    token: str,
    pattern: str,
    current: datetime,
    issues: list[TemplateIssue],
    label: str,
) -> str | None:
    if not pattern:
        issues.append(
            TemplateIssue("empty_format", token, f"{label} format cannot be empty.")
        )
        return None
    try:
        return current.strftime(pattern)
    except (ValueError, TypeError) as error:
        issues.append(TemplateIssue("invalid_format", token, str(error)))
        return None


def _split_escaped(value: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    escaped = False
    for character in value:
        if escaped:
            current.append(character)
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == "|":
            parts.append("".join(current))
            current = []
        else:
            current.append(character)
    if escaped:
        current.append("\\")
    parts.append("".join(current))
    return parts


def escape_field_part(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|")


def _parse_field(token: str) -> tuple[FormField | None, TemplateIssue | None]:
    kind_text, _, payload = token.partition(":")
    parts = _split_escaped(payload)
    identifier = parts[0].strip() if parts else ""
    if not FIELD_ID_RE.fullmatch(identifier):
        return None, TemplateIssue(
            "invalid_field",
            token,
            "Field identifier must start with a letter or underscore.",
        )
    label = parts[1].strip() if len(parts) > 1 and parts[1].strip() else identifier
    if kind_text == FormFieldKind.INPUT.value:
        if len(parts) > 3:
            return None, TemplateIssue("invalid_field", token, "Input field has too many parts.")
        return FormField(
            identifier=identifier,
            label=label,
            kind=FormFieldKind.INPUT,
            default=parts[2] if len(parts) > 2 else "",
        ), None
    if kind_text == FormFieldKind.CHOICE.value:
        options = tuple(part for part in parts[2:] if part)
        if not options:
            return None, TemplateIssue(
                "invalid_field",
                token,
                "Choice field requires at least one option.",
            )
        return FormField(
            identifier=identifier,
            label=label,
            kind=FormFieldKind.CHOICE,
            default=options[0],
            options=options,
        ), None
    if kind_text == FormFieldKind.CHECK.value:
        if len(parts) not in (3, 4):
            return None, TemplateIssue(
                "invalid_field",
                token,
                "Check field requires checked and optional unchecked text.",
            )
        return FormField(
            identifier=identifier,
            label=label,
            kind=FormFieldKind.CHECK,
            default=parts[2],
            checked_text=parts[2],
            unchecked_text=parts[3] if len(parts) > 3 else "",
        ), None
    return None, TemplateIssue("invalid_field", token, "Unknown field type.")


def _field_value(field: FormField, values: Mapping[str, str]) -> str:
    if field.identifier in values:
        return values[field.identifier]
    return field.default


def _collect_fields(
    template: str,
    fields: dict[str, FormField],
    issues: list[TemplateIssue],
    *,
    snippet_provider: SnippetProvider | None,
    stack: tuple[str, ...],
) -> None:
    for match in TOKEN_RE.finditer(template):
        token = match.group(1).strip()
        if token.startswith(("input:", "choice:", "check:")):
            field, issue = _parse_field(token)
            if issue is not None:
                issues.append(issue)
                continue
            assert field is not None
            existing = fields.get(field.identifier)
            if existing is not None and existing != field:
                issues.append(
                    TemplateIssue(
                        "field_conflict",
                        token,
                        f"Field '{field.identifier}' has conflicting definitions.",
                    )
                )
            else:
                fields.setdefault(field.identifier, field)
        elif token.startswith("snippet:") and snippet_provider is not None:
            abbreviation = token[8:].strip()
            if abbreviation in stack:
                issues.append(
                    TemplateIssue("snippet_cycle", token, "Snippet composition contains a cycle.")
                )
            elif len(stack) >= MAX_SNIPPET_DEPTH:
                issues.append(
                    TemplateIssue(
                        "snippet_depth",
                        token,
                        f"Snippet composition cannot exceed {MAX_SNIPPET_DEPTH} levels.",
                    )
                )
            else:
                nested = snippet_provider(abbreviation)
                if nested is None:
                    issues.append(
                        TemplateIssue(
                            "missing_snippet",
                            token,
                            f"Snippet '{abbreviation}' is missing or unavailable.",
                        )
                    )
                else:
                    _collect_fields(
                        nested,
                        fields,
                        issues,
                        snippet_provider=snippet_provider,
                        stack=stack + (abbreviation,),
                    )
    return None


def calculate_expression(
    expression: str,
    variables: Mapping[str, str] | None = None,
) -> str:
    if not expression:
        raise ValueError("Calculation cannot be empty.")
    if len(expression) > MAX_CALC_LENGTH:
        raise ValueError(f"Calculation cannot exceed {MAX_CALC_LENGTH} characters.")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as error:
        raise ValueError("Calculation has invalid syntax.") from error
    with localcontext() as context:
        context.prec = 50
        try:
            result = _evaluate_node(tree.body, variables or {}, depth=0)
        except (DivisionByZero, InvalidOperation, OverflowError) as error:
            raise ValueError("Calculation cannot be evaluated.") from error
    if not result.is_finite() or abs(result) > MAX_RESULT_MAGNITUDE:
        raise ValueError("Calculation result is outside the supported range.")
    normalized = result.normalize()
    if normalized == normalized.to_integral():
        return str(normalized.quantize(Decimal(1)))
    return format(normalized, "f").rstrip("0").rstrip(".")


def _evaluate_node(
    node: ast.AST,
    variables: Mapping[str, str],
    *,
    depth: int,
) -> Decimal:
    if depth > 30:
        raise ValueError("Calculation is too complex.")
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        if isinstance(node.value, bool):
            raise ValueError("Boolean values are not supported.")
        return Decimal(str(node.value))
    if isinstance(node, ast.Name):
        raw_value = variables.get(node.id)
        if raw_value is None:
            raise ValueError(f"Unknown calculation variable '{node.id}'.")
        try:
            return Decimal(raw_value.strip().replace(",", "."))
        except InvalidOperation as error:
            raise ValueError(f"Variable '{node.id}' is not a number.") from error
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _evaluate_node(node.operand, variables, depth=depth + 1)
        return value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.BinOp):
        left = _evaluate_node(node.left, variables, depth=depth + 1)
        right = _evaluate_node(node.right, variables, depth=depth + 1)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right
        if isinstance(node.op, ast.FloorDiv):
            return left // right
        if isinstance(node.op, ast.Mod):
            return left % right
        if isinstance(node.op, ast.Pow):
            if right != right.to_integral() or abs(right) > 12:
                raise ValueError("Exponent must be an integer between -12 and 12.")
            return left ** int(right)
    raise ValueError("Calculation contains an unsupported operation.")
