from __future__ import annotations

import hashlib
import json
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from .models import (
    Snippet,
    SnippetAsset,
    SnippetBundle,
    SnippetContentFormat,
    SnippetKind,
    TriggerMode,
    normalize_applications,
    normalize_priority,
    normalize_search_terms,
    validate_description,
    validate_snippet_trigger,
)

BACKUP_FORMAT = "quicktype-backup"
BACKUP_VERSION = 3
SUPPORTED_LEGACY_VERSIONS = frozenset({1, 2})
MAX_LEGACY_BACKUP_SIZE = 10 * 1024 * 1024
MAX_PACKAGE_SIZE = 500 * 1024 * 1024
MAX_MANIFEST_SIZE = 10 * 1024 * 1024
MAX_PACKAGE_ENTRIES = 10_000
MAX_ASSET_SIZE = 10 * 1024 * 1024
MAX_SNIPPET_ASSETS = 25 * 1024 * 1024
MAX_LIBRARY_ASSETS = 250 * 1024 * 1024
MANIFEST_NAME = "manifest.json"


class BackupFormatError(ValueError):
    pass


def export_backup(
    path: Path,
    snippets: Iterable[Snippet | SnippetBundle],
    *,
    library_state: dict[str, object] | None = None,
) -> None:
    bundles = tuple(
        item if isinstance(item, SnippetBundle) else SnippetBundle(item)
        for item in snippets
    )
    destination = Path(path)
    if destination.suffix.casefold() == ".json":
        _export_legacy_json(destination, bundles, library_state)
        return
    if destination.suffix.casefold() != ".qtbackup":
        destination = destination.with_suffix(".qtbackup")
    _export_package(destination, bundles, library_state)


def import_backup(path: Path) -> list[Snippet]:
    return [bundle.snippet for bundle in import_backup_bundles(path)]


def import_backup_bundles(path: Path) -> list[SnippetBundle]:
    source = Path(path)
    try:
        is_package = zipfile.is_zipfile(source)
    except OSError as error:
        raise BackupFormatError(str(error)) from error
    if is_package:
        bundles, _state = _read_package(source)
        return list(bundles)
    document = _read_legacy_document(source)
    snippets = _snippets_from_document(document)
    return [SnippetBundle(snippet) for snippet in snippets]


def import_library_state(path: Path) -> dict[str, object] | None:
    source = Path(path)
    try:
        is_package = zipfile.is_zipfile(source)
    except OSError as error:
        raise BackupFormatError(str(error)) from error
    if is_package:
        _bundles, state = _read_package(source)
    else:
        state = _read_legacy_document(source).get("library_state")
    if state is None:
        return None
    if not isinstance(state, dict):
        raise BackupFormatError("Backup contains invalid built-in library state.")
    return state


def _export_legacy_json(
    destination: Path,
    bundles: tuple[SnippetBundle, ...],
    library_state: dict[str, object] | None,
) -> None:
    if any(
        bundle.assets
        or bundle.snippet.content_format != SnippetContentFormat.PLAIN
        or bundle.snippet.rich_html
        for bundle in bundles
    ):
        raise BackupFormatError("Rich snippets must be exported as a .qtbackup package.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    document: dict[str, object] = {
        "format": BACKUP_FORMAT,
        "version": 2,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "snippets": [_snippet_to_dict(bundle.snippet) for bundle in bundles],
    }
    if library_state is not None:
        document["library_state"] = library_state
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(destination)


def _export_package(
    destination: Path,
    bundles: tuple[SnippetBundle, ...],
    library_state: dict[str, object] | None,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    asset_payloads: dict[str, tuple[str, bytes]] = {}
    snippet_documents: list[dict[str, Any]] = []
    total_assets = 0
    for bundle in bundles:
        raw = _snippet_to_dict(bundle.snippet)
        manifests: list[dict[str, object]] = []
        snippet_bytes = 0
        for asset in bundle.assets:
            _validate_asset(asset)
            extension = ".png" if asset.mime_type == "image/png" else ".jpg"
            asset_path = f"assets/{asset.sha256}{extension}"
            previous = asset_payloads.get(asset.sha256)
            if previous is not None and previous != (asset_path, asset.data):
                raise BackupFormatError("Conflicting image checksums in backup data.")
            asset_payloads[asset.sha256] = (asset_path, asset.data)
            manifests.append(
                {
                    "asset_id": asset.asset_id,
                    "path": asset_path,
                    "mime_type": asset.mime_type,
                    "original_name": asset.original_name,
                    "width": asset.width,
                    "height": asset.height,
                    "sha256": asset.sha256,
                }
            )
            snippet_bytes += len(asset.data)
        if snippet_bytes > MAX_SNIPPET_ASSETS:
            raise BackupFormatError("A snippet contains more than 25 MiB of images.")
        total_assets += snippet_bytes
        raw["content_format"] = bundle.snippet.content_format.value
        raw["rich_html"] = bundle.snippet.rich_html
        raw["assets"] = manifests
        snippet_documents.append(raw)
    if total_assets > MAX_LIBRARY_ASSETS:
        raise BackupFormatError("The image library exceeds 250 MiB.")
    document: dict[str, object] = {
        "format": BACKUP_FORMAT,
        "version": BACKUP_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "snippets": snippet_documents,
    }
    if library_state is not None:
        document["library_state"] = library_state
    manifest = (
        json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    if len(manifest) > MAX_MANIFEST_SIZE:
        raise BackupFormatError("Backup manifest is larger than 10 MiB.")
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    try:
        with zipfile.ZipFile(
            temporary,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as archive:
            archive.writestr(MANIFEST_NAME, manifest)
            for asset_path, data in sorted(asset_payloads.values()):
                archive.writestr(asset_path, data)
        if temporary.stat().st_size > MAX_PACKAGE_SIZE:
            raise BackupFormatError("Backup package is larger than 500 MiB.")
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _read_package(
    source: Path,
) -> tuple[tuple[SnippetBundle, ...], dict[str, object] | None]:
    try:
        if source.stat().st_size > MAX_PACKAGE_SIZE:
            raise BackupFormatError("Backup package is larger than 500 MiB.")
        with zipfile.ZipFile(source, "r") as archive:
            infos = archive.infolist()
            if len(infos) > MAX_PACKAGE_ENTRIES:
                raise BackupFormatError("Backup package contains too many files.")
            names: set[str] = set()
            total_uncompressed = 0
            for info in infos:
                _validate_archive_name(info.filename)
                if info.filename in names:
                    raise BackupFormatError("Backup package contains duplicate file names.")
                names.add(info.filename)
                total_uncompressed += info.file_size
                if total_uncompressed > MAX_PACKAGE_SIZE:
                    raise BackupFormatError("Expanded backup package exceeds 500 MiB.")
            if MANIFEST_NAME not in names:
                raise BackupFormatError("Backup package has no manifest.json.")
            manifest_info = archive.getinfo(MANIFEST_NAME)
            if manifest_info.file_size > MAX_MANIFEST_SIZE:
                raise BackupFormatError("Backup manifest is larger than 10 MiB.")
            try:
                document = json.loads(archive.read(MANIFEST_NAME).decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError) as error:
                raise BackupFormatError(str(error)) from error
            _validate_v3_document(document)
            cache: dict[str, bytes] = {}
            bundles = tuple(
                _bundle_from_manifest(raw, index, archive, names, cache)
                for index, raw in enumerate(document["snippets"])
            )
    except (OSError, zipfile.BadZipFile, KeyError) as error:
        raise BackupFormatError(str(error)) from error
    abbreviations = [bundle.snippet.abbreviation for bundle in bundles]
    if len(abbreviations) != len(set(abbreviations)):
        raise BackupFormatError("Backup contains duplicate abbreviations.")
    total_assets = sum(len(asset.data) for bundle in bundles for asset in bundle.assets)
    if total_assets > MAX_LIBRARY_ASSETS:
        raise BackupFormatError("The image library exceeds 250 MiB.")
    state = document.get("library_state")
    if state is not None and not isinstance(state, dict):
        raise BackupFormatError("Backup contains invalid built-in library state.")
    return bundles, state


def _validate_v3_document(value: object) -> None:
    if not isinstance(value, dict):
        raise BackupFormatError("Backup manifest root must be an object.")
    if value.get("format") != BACKUP_FORMAT or value.get("version") != BACKUP_VERSION:
        raise BackupFormatError("Unsupported QuickType backup package.")
    if not isinstance(value.get("snippets"), list):
        raise BackupFormatError("Backup manifest does not contain a snippets list.")


def _bundle_from_manifest(
    value: object,
    index: int,
    archive: zipfile.ZipFile,
    archive_names: set[str],
    cache: dict[str, bytes],
) -> SnippetBundle:
    snippet = _snippet_from_dict(value, index, allow_rich=True)
    assert isinstance(value, dict)
    raw_assets = value.get("assets", [])
    if not isinstance(raw_assets, list):
        raise BackupFormatError(f"Snippet #{index + 1} has invalid image assets.")
    assets: list[SnippetAsset] = []
    total_bytes = 0
    for raw_asset in raw_assets:
        if not isinstance(raw_asset, dict):
            raise BackupFormatError(f"Snippet #{index + 1} has an invalid image asset.")
        required = (
            "asset_id",
            "path",
            "mime_type",
            "original_name",
            "width",
            "height",
            "sha256",
        )
        if any(key not in raw_asset for key in required):
            raise BackupFormatError(f"Snippet #{index + 1} has an incomplete image asset.")
        asset_path = raw_asset["path"]
        sha256 = raw_asset["sha256"]
        if not isinstance(asset_path, str) or not isinstance(sha256, str):
            raise BackupFormatError(f"Snippet #{index + 1} has an invalid image asset.")
        if asset_path not in archive_names or not asset_path.startswith("assets/"):
            raise BackupFormatError(f"Snippet #{index + 1} references a missing image.")
        data = cache.get(asset_path)
        if data is None:
            info = archive.getinfo(asset_path)
            if info.file_size > MAX_ASSET_SIZE:
                raise BackupFormatError("An image exceeds the 10 MiB limit.")
            data = archive.read(asset_path)
            cache[asset_path] = data
        if hashlib.sha256(data).hexdigest() != sha256:
            raise BackupFormatError("An image checksum does not match its data.")
        asset = SnippetAsset(
            asset_id=str(raw_asset["asset_id"]),
            mime_type=str(raw_asset["mime_type"]),
            data=data,
            original_name=str(raw_asset["original_name"]),
            width=_positive_int(raw_asset["width"], "image width"),
            height=_positive_int(raw_asset["height"], "image height"),
            sha256=sha256,
        )
        _validate_asset(asset)
        assets.append(asset)
        total_bytes += len(data)
    if total_bytes > MAX_SNIPPET_ASSETS:
        raise BackupFormatError("A snippet contains more than 25 MiB of images.")
    identifiers = {asset.asset_id for asset in assets}
    referenced = set(_asset_references(snippet.rich_html))
    if snippet.content_format == SnippetContentFormat.PLAIN:
        if snippet.rich_html or assets:
            raise BackupFormatError("A plain snippet cannot contain rich content.")
    elif identifiers != referenced:
        raise BackupFormatError("Rich HTML and embedded image assets are inconsistent.")
    return SnippetBundle(snippet=snippet, assets=tuple(assets))


def _read_legacy_document(path: Path) -> dict[str, Any]:
    source = Path(path)
    try:
        if source.stat().st_size > MAX_LEGACY_BACKUP_SIZE:
            raise BackupFormatError("Legacy backup file is larger than 10 MiB.")
        document = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise BackupFormatError(str(error)) from error
    if not isinstance(document, dict):
        raise BackupFormatError("Backup root must be a JSON object.")
    if document.get("format") != BACKUP_FORMAT:
        raise BackupFormatError("This is not a QuickType backup.")
    if document.get("version") not in SUPPORTED_LEGACY_VERSIONS:
        raise BackupFormatError("Unsupported QuickType backup version.")
    if not isinstance(document.get("snippets"), list):
        raise BackupFormatError("Backup does not contain a snippets list.")
    return document


def _snippets_from_document(document: dict[str, Any]) -> list[Snippet]:
    raw_snippets = document["snippets"]
    snippets = [
        _snippet_from_dict(item, index, allow_rich=False)
        for index, item in enumerate(raw_snippets)
    ]
    abbreviations = [snippet.abbreviation for snippet in snippets]
    if len(abbreviations) != len(set(abbreviations)):
        raise BackupFormatError("Backup contains duplicate abbreviations.")
    return snippets


def _snippet_to_dict(snippet: Snippet) -> dict[str, Any]:
    return {
        "abbreviation": snippet.abbreviation,
        "expansion": snippet.expansion,
        "trigger_mode": snippet.trigger_mode.value,
        "enabled": snippet.enabled,
        "created_at": snippet.created_at.isoformat(timespec="seconds")
        if snippet.created_at
        else None,
        "updated_at": snippet.updated_at.isoformat(timespec="seconds")
        if snippet.updated_at
        else None,
        "usage_count": snippet.usage_count,
        "last_used_at": snippet.last_used_at.isoformat(timespec="seconds")
        if snippet.last_used_at
        else None,
        "category": snippet.category,
        "favorite": snippet.favorite,
        "applications": list(snippet.applications),
        "kind": snippet.kind.value,
        "description": snippet.description,
        "search_terms": list(snippet.search_terms),
        "priority": snippet.priority,
    }


def _snippet_from_dict(value: object, index: int, *, allow_rich: bool) -> Snippet:
    if not isinstance(value, dict):
        raise BackupFormatError(f"Snippet #{index + 1} must be an object.")
    abbreviation = value.get("abbreviation")
    expansion = value.get("expansion")
    trigger_mode = value.get("trigger_mode")
    enabled = value.get("enabled")
    usage_count = value.get("usage_count", 0)
    category = value.get("category", "")
    favorite = value.get("favorite", False)
    applications = value.get("applications", [])
    kind = value.get("kind", SnippetKind.LITERAL.value)
    description = value.get("description", "")
    search_terms = value.get("search_terms", [])
    priority = value.get("priority", 0)
    content_format = value.get("content_format", SnippetContentFormat.PLAIN.value)
    rich_html = value.get("rich_html", "")

    if not isinstance(abbreviation, str) or not isinstance(expansion, str):
        raise BackupFormatError(f"Snippet #{index + 1} has invalid text fields.")
    if not isinstance(trigger_mode, str):
        raise BackupFormatError(f"Snippet #{index + 1} has an invalid trigger mode.")
    try:
        mode = TriggerMode(trigger_mode)
        snippet_kind = SnippetKind(str(kind))
        parsed_content_format = SnippetContentFormat(str(content_format))
    except ValueError as error:
        raise BackupFormatError(f"Snippet #{index + 1} has an invalid enum value.") from error
    if not allow_rich and parsed_content_format != SnippetContentFormat.PLAIN:
        raise BackupFormatError("Legacy JSON backups cannot contain rich snippets.")
    if not isinstance(rich_html, str):
        raise BackupFormatError(f"Snippet #{index + 1} has invalid rich HTML.")
    if not isinstance(enabled, bool):
        raise BackupFormatError(f"Snippet #{index + 1} has an invalid enabled value.")
    if not isinstance(usage_count, int) or isinstance(usage_count, bool) or usage_count < 0:
        raise BackupFormatError(f"Snippet #{index + 1} has an invalid usage count.")
    if (
        not isinstance(category, str)
        or len(category.strip()) > 64
        or any(character in "\r\n" or ord(character) < 32 for character in category)
    ):
        raise BackupFormatError(f"Snippet #{index + 1} has an invalid category.")
    if not isinstance(favorite, bool):
        raise BackupFormatError(f"Snippet #{index + 1} has an invalid favorite value.")
    if not isinstance(applications, list) or not all(isinstance(item, str) for item in applications):
        raise BackupFormatError(f"Snippet #{index + 1} has invalid applications.")
    if not isinstance(description, str) or validate_description(description):
        raise BackupFormatError(f"Snippet #{index + 1} has an invalid description.")
    if not isinstance(search_terms, list) or not all(isinstance(item, str) for item in search_terms):
        raise BackupFormatError(f"Snippet #{index + 1} has invalid search terms.")
    if validate_snippet_trigger(abbreviation, snippet_kind):
        raise BackupFormatError(f"Snippet #{index + 1} has an invalid abbreviation.")
    try:
        normalized_applications = normalize_applications(applications)
        normalized_search_terms = normalize_search_terms(search_terms)
        normalized_priority = normalize_priority(priority)
    except ValueError as error:
        raise BackupFormatError(f"Snippet #{index + 1} has invalid settings.") from error
    return Snippet(
        id=None,
        abbreviation=abbreviation,
        expansion=expansion,
        trigger_mode=mode,
        enabled=enabled,
        created_at=_optional_datetime(value.get("created_at"), index, "created_at"),
        updated_at=_optional_datetime(value.get("updated_at"), index, "updated_at"),
        usage_count=usage_count,
        last_used_at=_optional_datetime(value.get("last_used_at"), index, "last_used_at"),
        category=category.strip(),
        favorite=favorite,
        applications=normalized_applications,
        kind=snippet_kind,
        description=description.strip(),
        search_terms=normalized_search_terms,
        priority=normalized_priority,
        content_format=parsed_content_format,
        rich_html=rich_html,
    )


def _validate_asset(asset: SnippetAsset) -> None:
    try:
        canonical_id = str(uuid.UUID(asset.asset_id))
    except (ValueError, AttributeError) as error:
        raise BackupFormatError("An image asset has an invalid identifier.") from error
    if canonical_id != asset.asset_id:
        raise BackupFormatError("An image asset identifier is not canonical.")
    if asset.mime_type not in {"image/png", "image/jpeg"}:
        raise BackupFormatError("An image asset has an unsupported MIME type.")
    if not asset.data or len(asset.data) > MAX_ASSET_SIZE:
        raise BackupFormatError("An image exceeds the 10 MiB limit.")
    if hashlib.sha256(asset.data).hexdigest() != asset.sha256:
        raise BackupFormatError("An image checksum does not match its data.")
    if not (0 < asset.width <= 16384 and 0 < asset.height <= 16384):
        raise BackupFormatError("An image has invalid dimensions.")
    if (
        not asset.original_name
        or len(asset.original_name) > 255
        or Path(asset.original_name).name != asset.original_name
        or any(ord(character) < 32 for character in asset.original_name)
    ):
        raise BackupFormatError("An image has an invalid file name.")


def _asset_references(value: str) -> tuple[str, ...]:
    import re

    return tuple(re.findall(r"quicktype-asset://([0-9a-fA-F-]{36})", value))


def _validate_archive_name(value: str) -> None:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value or not path.parts:
        raise BackupFormatError("Backup package contains an unsafe path.")
    if value != MANIFEST_NAME and not value.startswith("assets/"):
        raise BackupFormatError("Backup package contains an unexpected file.")


def _positive_int(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 < value <= 16384:
        raise BackupFormatError(f"Invalid {label}.")
    return value


def _optional_datetime(value: object, index: int, field: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise BackupFormatError(f"Snippet #{index + 1} has an invalid {field}.")
    try:
        return datetime.fromisoformat(value)
    except ValueError as error:
        raise BackupFormatError(f"Snippet #{index + 1} has an invalid {field}.") from error
