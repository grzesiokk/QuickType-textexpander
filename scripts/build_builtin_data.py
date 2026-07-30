from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import re
import tempfile
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Iterable

UNICODE_VERSION = "17.0"
CLDR_VERSION = "48.0.0"
LANGUAGETOOL_COMMIT = "517f7ad765ee8bf92e90e3d3b872bfd82690c65b"
SOURCES = {
    "emoji-test.txt": "https://www.unicode.org/Public/17.0.0/emoji/emoji-test.txt",
    "annotations-pl.json": (
        "https://raw.githubusercontent.com/unicode-org/cldr-json/"
        f"{CLDR_VERSION}/cldr-json/cldr-annotations-full/annotations/pl/annotations.json"
    ),
    "annotations-derived-pl.json": (
        "https://raw.githubusercontent.com/unicode-org/cldr-json/"
        f"{CLDR_VERSION}/cldr-json/cldr-annotations-derived-full/"
        "annotationsDerived/pl/annotations.json"
    ),
    "territories-pl.json": (
        "https://raw.githubusercontent.com/unicode-org/cldr-json/"
        f"{CLDR_VERSION}/cldr-json/cldr-localenames-full/main/pl/territories.json"
    ),
    "PL.zip": "https://download.geonames.org/export/zip/PL.zip",
    "common_words.txt": (
        "https://raw.githubusercontent.com/languagetool-org/languagetool/"
        f"{LANGUAGETOOL_COMMIT}/languagetool-language-modules/pl/src/main/resources/"
        "org/languagetool/resource/pl/common_words.txt"
    ),
    "grammar.xml": (
        "https://raw.githubusercontent.com/languagetool-org/languagetool/"
        f"{LANGUAGETOOL_COMMIT}/languagetool-language-modules/pl/src/main/resources/"
        "org/languagetool/rules/pl/grammar.xml"
    ),
}
SOURCE_SHA256 = {
    "emoji-test.txt": "1d8a944f88d7952f7ef7c5167fef3c67995bcae24543949710231b03a201acda",
    "annotations-pl.json": "81420256d40acb05a9a512cdde5c67bafa6a373df51c93a83f02ecc200cba6fa",
    "annotations-derived-pl.json": (
        "489db8825f8a02737595a94b0b0e6a165acbc3705f6207a4fe0aa064a645362d"
    ),
    "territories-pl.json": "ae91e6cb2c761bc158f08ae3bf9a6d495b05914ac945cc6eea1bee2b8b532aab",
    "PL.zip": "57faf0d308aeb8336e48cbf34869c5fbf871562f89187322699eecc9315de47e",
    "common_words.txt": "f984a07cfd89254557e7e1f7cdbf1bf96b0fe281bb7dcbbf715b0ff977227a0b",
    "grammar.xml": "4755d9deaa6a52621428298aec2d4a70f6b49c2d78bf010aa864ba897d7f64b7",
}
POLISH_LETTERS_RE = re.compile(r"[a-ząćęłńóśźż-]+\Z", re.IGNORECASE)
VOIVODESHIPS = {
    "72": "dolnośląskie",
    "73": "kujawsko-pomorskie",
    "74": "łódzkie",
    "75": "lubelskie",
    "76": "lubuskie",
    "77": "małopolskie",
    "78": "mazowieckie",
    "79": "opolskie",
    "80": "podkarpackie",
    "81": "podlaskie",
    "82": "pomorskie",
    "83": "śląskie",
    "84": "świętokrzyskie",
    "85": "warmińsko-mazurskie",
    "86": "wielkopolskie",
    "87": "zachodniopomorskie",
}
MANUAL_CORRECTIONS = {
    "a propo": "à propos",
    "abstracyjny": "abstrakcyjny",
    "adekwatyny": "adekwatny",
    "agresywny": "agresywny",
    "analfabetyzm": "analfabetyzm",
    "apropos": "à propos",
    "austryjacki": "austriacki",
    "błachy": "błahy",
    "błędów": "błędów",
    "bynajmiej": "bynajmniej",
    "chańba": "hańba",
    "chistoria": "historia",
    "chonor": "honor",
    "chrabia": "hrabia",
    "codzień": "co dzień",
    "conajmniej": "co najmniej",
    "conajwyżej": "co najwyżej",
    "córka": "córka",
    "czeba": "trzeba",
    "czychać": "czyhać",
    "doktór": "doktor",
    "dopuki": "dopóki",
    "drużyna": "drużyna",
    "dzwięk": "dźwięk",
    "emaila": "e-maila",
    "ewentualnie": "ewentualnie",
    "fabryka": "fabryka",
    "faktór": "faktur",
    "higiena": "higiena",
    "hoży": "hoży",
    "jakby nie było": "jakkolwiek by było",
    "jusz": "już",
    "ktury": "który",
    "kture": "które",
    "którzy": "którzy",
    "kużnia": "kuźnia",
    "marihuana": "marihuana",
    "męszczyzna": "mężczyzna",
    "mimo wszystko": "mimo wszystko",
    "na codzień": "na co dzień",
    "na prawdę": "naprawdę",
    "napewno": "na pewno",
    "narazie": "na razie",
    "na przeciwko": "naprzeciwko",
    "niewiem": "nie wiem",
    "nie można": "nie można",
    "niechcem": "nie chcę",
    "niekturzy": "niektórzy",
    "niema": "nie ma",
    "niemoge": "nie mogę",
    "niepotrafię": "nie potrafię",
    "nieumiem": "nie umiem",
    "niewolno": "nie wolno",
    "obrzudny": "obrzydliwy",
    "ochydny": "ohydny",
    "odrazu": "od razu",
    "orginalny": "oryginalny",
    "po za": "poza",
    "poprostu": "po prostu",
    "porzegnać": "pożegnać",
    "puki": "póki",
    "puźniej": "później",
    "rzaden": "żaden",
    "rzadko": "rzadko",
    "rzeczywiście": "rzeczywiście",
    "skąd inąd": "skądinąd",
    "spowrotem": "z powrotem",
    "sprubować": "spróbować",
    "sześćdziesiąt": "sześćdziesiąt",
    "tesz": "też",
    "tą książkę": "tę książkę",
    "wogóle": "w ogóle",
    "wziąść": "wziąć",
    "z kąd": "skąd",
    "z przed": "sprzed",
    "z tąd": "stąd",
    "za razem": "zarazem",
    "żeka": "rzeka",
    "żetelny": "rzetelny",
    "żócić": "rzucić",
    "żółw": "żółw",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=Path(tempfile.gettempdir()) / "quicktype-2-builtin-sources",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "src" / "quicktype" / "resources",
    )
    options = parser.parse_args()
    options.source_dir.mkdir(parents=True, exist_ok=True)
    options.output_dir.mkdir(parents=True, exist_ok=True)
    _download_sources(options.source_dir)

    emoji, flags = _build_emoji_and_flags(options.source_dir)
    postal = _build_postal(options.source_dir)
    autocorrect = _build_autocorrect(options.source_dir)

    _write_catalog(
        options.output_dir / "builtin_emoji.json.gz",
        "emoji",
        emoji,
        source=f"Unicode Emoji {UNICODE_VERSION}; CLDR {CLDR_VERSION}",
        license_name="Unicode License v3",
    )
    _write_catalog(
        options.output_dir / "builtin_flags.json.gz",
        "flags",
        flags,
        source=f"Unicode Emoji {UNICODE_VERSION}; CLDR {CLDR_VERSION}",
        license_name="Unicode License v3",
    )
    _write_catalog(
        options.output_dir / "builtin_postal_pl.json.gz",
        "postal_pl",
        postal,
        source="GeoNames PL postal-code dump",
        license_name="CC BY 4.0",
    )
    _write_catalog(
        options.output_dir / "builtin_autocorrect_pl.json.gz",
        "autocorrect_pl",
        autocorrect,
        source=f"LanguageTool Polish resources at {LANGUAGETOOL_COMMIT}",
        license_name="LGPL-2.1-or-later / source dictionary notices",
    )
    print(
        json.dumps(
            {
                "emoji": len(emoji),
                "flags": len(flags),
                "postal_pl": len(postal),
                "autocorrect_pl": len(autocorrect),
                "autocorrect_conservative": sum(
                    item["profile"] == "conservative"
                    for item in autocorrect
                ),
            },
            ensure_ascii=False,
        )
    )
    return 0


def _download_sources(source_dir: Path) -> None:
    for name, url in SOURCES.items():
        destination = source_dir / name
        if not destination.exists():
            print(f"Downloading {url}")
            with urllib.request.urlopen(url, timeout=60) as response:
                destination.write_bytes(response.read())
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        if digest != SOURCE_SHA256[name]:
            raise ValueError(
                f"Source checksum mismatch for {name}: {digest}"
            )


def _build_emoji_and_flags(
    source_dir: Path,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    annotations = json.loads(
        (source_dir / "annotations-pl.json").read_text(encoding="utf-8")
    )["annotations"]["annotations"]
    derived = json.loads(
        (source_dir / "annotations-derived-pl.json").read_text(encoding="utf-8")
    )["annotationsDerived"]["annotations"]
    annotations.update(derived)
    territories = json.loads(
        (source_dir / "territories-pl.json").read_text(encoding="utf-8")
    )["main"]["pl"]["localeDisplayNames"]["territories"]

    emoji_items: list[dict[str, object]] = []
    flag_items: list[dict[str, object]] = []
    group = ""
    used_slugs: set[str] = set()
    for line in (source_dir / "emoji-test.txt").read_text(encoding="utf-8").splitlines():
        if line.startswith("# group:"):
            group = line.partition(":")[2].strip()
            continue
        if "; fully-qualified" not in line:
            continue
        data, _, comment = line.partition("#")
        codepoints = data.partition(";")[0].strip().split()
        value = "".join(chr(int(codepoint, 16)) for codepoint in codepoints)
        english_name = comment.split(" E", 1)[-1].split(" ", 1)[-1].strip()
        if group == "Flags" and _is_country_flag(codepoints):
            country_code = "".join(
                chr(int(codepoint, 16) - 0x1F1E6 + ord("A"))
                for codepoint in codepoints
            )
            title = str(territories.get(country_code, english_name))
            flag_items.append(
                {
                    "id": f"flag-{country_code.casefold()}",
                    "title": title,
                    "expansion": value,
                    "slug": country_code.casefold(),
                    "keywords": _unique_strings(
                        (title, country_code, english_name, "flaga", "państwo")
                    ),
                    "profile": "full",
                }
            )
            continue
        annotation = annotations.get(value) or annotations.get(
            value.replace("\ufe0f", "")
        )
        title = (
            str(annotation["tts"][0])
            if annotation and annotation.get("tts")
            else english_name
        )
        keywords = (
            list(annotation.get("default", []))
            if annotation
            else []
        )
        slug = _unique_slug(_slugify(title), used_slugs, codepoints)
        emoji_items.append(
            {
                "id": "emoji-" + "-".join(codepoint.casefold() for codepoint in codepoints),
                "title": title,
                "expansion": value,
                "slug": slug,
                "keywords": _unique_strings((*keywords, english_name, group, "emoji")),
                "profile": "full",
            }
        )
    return emoji_items, flag_items


def _is_country_flag(codepoints: list[str]) -> bool:
    return len(codepoints) == 2 and all(
        0x1F1E6 <= int(codepoint, 16) <= 0x1F1FF
        for codepoint in codepoints
    )


def _build_postal(source_dir: Path) -> list[dict[str, object]]:
    with zipfile.ZipFile(source_dir / "PL.zip") as archive:
        rows = archive.read("PL.txt").decode("utf-8").splitlines()
    items: list[dict[str, object]] = []
    seen_rows: set[tuple[str, str, str, str, str]] = set()
    used_triggers: set[str] = set()
    for row in rows:
        fields = row.split("\t")
        if len(fields) < 9:
            continue
        (
            _country,
            postal_code,
            place,
            _admin_name1,
            admin_code1,
            admin_name2,
            _admin_code2,
            admin_name3,
            _admin_code3,
            *_rest,
        ) = fields
        voivodeship = VOIVODESHIPS.get(admin_code1, "")
        key = (postal_code, place, voivodeship, admin_name2, admin_name3)
        if key in seen_rows:
            continue
        seen_rows.add(key)
        base_slug = f"{_slugify(place)}-{postal_code}"
        trigger_slug = base_slug
        if trigger_slug in used_triggers:
            suffix = _slugify(admin_name3 or admin_name2 or voivodeship)
            trigger_slug = f"{base_slug}-{suffix}" if suffix else base_slug
        if trigger_slug in used_triggers:
            digest = hashlib.sha1("|".join(key).encode("utf-8")).hexdigest()[:8]
            trigger_slug = f"{base_slug}-{digest}"
        used_triggers.add(trigger_slug)
        digest = hashlib.sha1("|".join(key).encode("utf-8")).hexdigest()[:16]
        items.append(
            {
                "id": f"postal-{postal_code}-{digest}",
                "title": f"{postal_code} — {place}",
                "expansion": postal_code,
                "slug": trigger_slug,
                "keywords": _unique_strings(
                    (
                        postal_code,
                        place,
                        voivodeship,
                        admin_name2,
                        admin_name3,
                        "kod pocztowy",
                    )
                ),
                "profile": "full",
            }
        )
    return items


def _build_autocorrect(source_dir: Path) -> list[dict[str, object]]:
    common_words = [
        line.strip().casefold()
        for line in (source_dir / "common_words.txt").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip() and POLISH_LETTERS_RE.fullmatch(line.strip())
    ]
    valid_words = set(common_words)
    word_rank = {
        word: index
        for index, word in enumerate(common_words)
    }
    candidates: dict[str, tuple[str, int, str]] = {}

    def add(wrong: str, right: str, rank: int, origin: str) -> None:
        wrong = wrong.strip().casefold()
        right = right.strip().casefold()
        if (
            wrong == right
            or len(wrong) < 3
            or len(wrong) > 64
            or any(character.isspace() for character in wrong)
            or wrong in valid_words
            or not POLISH_LETTERS_RE.fullmatch(wrong.replace(" ", "-"))
        ):
            return
        current = candidates.get(wrong)
        if current is None or rank < current[1]:
            candidates[wrong] = (right, rank, origin)

    for wrong, right in MANUAL_CORRECTIONS.items():
        if wrong != right:
            add(wrong, right, 0, "curated")
    for wrong, right in _language_tool_example_pairs(source_dir / "grammar.xml"):
        add(wrong, right, 1, "languagetool")

    generated: dict[str, set[str]] = defaultdict(set)
    for right in common_words[:8000]:
        if not (4 <= len(right) <= 24) or "-" in right:
            continue
        generated[right + right[-1]].add(right)
        middle = len(right) // 2
        generated[right[:middle] + right[middle] + right[middle:]].add(right)
        for index in range(1, len(right) - 1):
            if right[index] == right[index + 1]:
                continue
            transposed = (
                right[:index]
                + right[index + 1]
                + right[index]
                + right[index + 2 :]
            )
            generated[transposed].add(right)
        for index in range(1, len(right) - 1):
            generated[right[:index] + right[index + 1 :]].add(right)
        ascii_variant = _strip_diacritics(right)
        if ascii_variant != right:
            generated[ascii_variant].add(right)

    safe_generated = [
        (wrong, next(iter(rights)))
        for wrong, rights in generated.items()
        if len(rights) == 1
        and wrong not in valid_words
        and len(wrong) >= 3
    ]
    safe_generated.sort(
        key=lambda pair: (
            word_rank.get(pair[1], len(common_words)),
            pair[0],
        )
    )
    for wrong, right in safe_generated:
        add(wrong, right, 2, "generated-from-languagetool-frequency-list")

    ordered = sorted(
        candidates.items(),
        key=lambda item: (
            item[1][1],
            word_rank.get(item[1][0], len(common_words)),
            item[0],
        ),
    )
    selected = ordered[:3000]
    if len(selected) < 3000:
        raise RuntimeError(f"Only {len(selected)} safe autocorrections were generated.")
    items: list[dict[str, object]] = []
    for index, (wrong, (right, _rank, origin)) in enumerate(selected):
        digest = hashlib.sha1(f"{wrong}|{right}".encode("utf-8")).hexdigest()[:16]
        items.append(
            {
                "id": f"autocorrect-{digest}",
                "title": f"{wrong} → {right}",
                "expansion": right,
                "slug": wrong,
                "keywords": [wrong, right, "autokorekta", "literówka", origin],
                "profile": "conservative" if index < 800 else "extended",
            }
        )
    return items


def _language_tool_example_pairs(path: Path) -> Iterable[tuple[str, str]]:
    root = ET.parse(path).getroot()
    pairs: dict[str, str] = {}
    for example in root.iter("example"):
        correction = example.get("correction")
        marker = example.find("marker")
        if correction is None or marker is None:
            continue
        wrong = "".join(marker.itertext()).strip().casefold()
        corrections = [
            value.strip().casefold()
            for value in correction.split("|")
            if value.strip()
        ]
        if (
            len(corrections) == 1
            and POLISH_LETTERS_RE.fullmatch(wrong)
            and POLISH_LETTERS_RE.fullmatch(corrections[0].replace(" ", "-"))
        ):
            pairs[wrong] = corrections[0]
    return pairs.items()


def _strip_diacritics(value: str) -> str:
    translated = value.translate(str.maketrans({"ł": "l", "Ł": "L"}))
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", translated)
        if not unicodedata.combining(character)
    )


def _slugify(value: str) -> str:
    normalized = _strip_diacritics(value).casefold()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    return slug or "item"


def _unique_slug(
    base: str,
    used: set[str],
    codepoints: list[str],
) -> str:
    if base not in used:
        used.add(base)
        return base
    suffix = "-".join(codepoint.casefold() for codepoint in codepoints)
    candidate = f"{base}-{suffix}"
    used.add(candidate)
    return candidate


def _unique_strings(values: Iterable[str]) -> list[str]:
    unique: dict[str, str] = {}
    for raw_value in values:
        value = str(raw_value).strip()
        if value:
            unique.setdefault(value.casefold(), value)
    return list(unique.values())


def _write_catalog(
    destination: Path,
    library_id: str,
    items: list[dict[str, object]],
    *,
    source: str,
    license_name: str,
) -> None:
    for item in items:
        keywords = item.get("keywords", [])
        searchable = " ".join(
            str(value)
            for value in (
                item.get("title", ""),
                item.get("slug", ""),
                item.get("expansion", ""),
                *(keywords if isinstance(keywords, list) else []),
            )
        )
        item["search"] = " ".join(
            _strip_diacritics(searchable).casefold().split()
        )
    document = {
        "format": "quicktype-builtin-library",
        "version": 1,
        "library_id": library_id,
        "source": source,
        "license": license_name,
        "items": items,
    }
    encoded = (
        json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as archive:
        archive.write(encoded)
    destination.write_bytes(buffer.getvalue())


if __name__ == "__main__":
    raise SystemExit(main())
