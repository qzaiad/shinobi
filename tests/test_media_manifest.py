"""Static checks on media/manifest.tsv and media/SOURCES.md (no network)."""

import re
from pathlib import Path

import pytest

MEDIA = Path(__file__).resolve().parents[1] / "media"
MANIFEST = MEDIA / "manifest.tsv"
SOURCES = MEDIA / "SOURCES.md"

EXPECTED_COLUMNS = [
    "id",
    "role",
    "filename",
    "url",
    "sha256",
    "license",
    "author",
    "source_page",
    "retrieved",
    "notes",
]
LICENSE_ALLOWLIST = {
    "CC0-1.0",
    *(f"CC-BY-{v}" for v in ("2.0", "3.0", "4.0")),
    *(f"CC-BY-SA-{v}" for v in ("2.0", "3.0", "4.0")),
    "PD",
    "Pexels",
    "Pixabay",
}
REQUIRED_ROLES = {"gate", "quay", "yard", "waterway"}
SHA256_RE = re.compile(r"[0-9a-f]{64}")
NC_ND_RE = re.compile(r"(?<![A-Z])N[CD](?![A-Z])")


def _data_lines(text: str) -> list[str]:
    lines = [line.rstrip("\r") for line in text.splitlines()[1:]]
    return [line for line in lines if line.strip() and not line.startswith("#")]


def _load() -> tuple[list[str], list[dict[str, str]]]:
    text = MANIFEST.read_text(encoding="utf-8")
    header = text.splitlines()[0].rstrip("\r").split("\t")
    rows = []
    for line in _data_lines(text):
        cells = line.split("\t")
        cells += [""] * (len(header) - len(cells))
        rows.append(dict(zip(header, cells, strict=False)))
    return header, rows


HEADER, ROWS = _load()
ROW_IDS = [row.get("id", "?") for row in ROWS]


def test_header_matches_expected_columns() -> None:
    assert HEADER == EXPECTED_COLUMNS


def test_rows_have_header_width() -> None:
    text = MANIFEST.read_text(encoding="utf-8")
    for line in _data_lines(text):
        assert len(line.split("\t")) == len(HEADER), f"wrong column count: {line!r}"


@pytest.mark.parametrize("column", ["id", "filename"])
def test_values_unique(column: str) -> None:
    values = [row[column] for row in ROWS]
    dupes = sorted({v for v in values if values.count(v) > 1})
    assert not dupes, f"duplicate {column}: {dupes}"


@pytest.mark.parametrize("row", ROWS, ids=ROW_IDS)
def test_sha256_is_lowercase_hex(row: dict[str, str]) -> None:
    assert SHA256_RE.fullmatch(row["sha256"]), (
        f"{row['id']}: sha256 {row['sha256']!r} is not 64 lowercase hex chars "
        "(pin it with scripts/fetch_media.sh --pin)"
    )


@pytest.mark.parametrize("row", ROWS, ids=ROW_IDS)
def test_url_is_https(row: dict[str, str]) -> None:
    assert row["url"].startswith("https://"), f"{row['id']}: {row['url']!r}"


@pytest.mark.parametrize("row", ROWS, ids=ROW_IDS)
def test_license_allowed(row: dict[str, str]) -> None:
    lic = row["license"]
    assert not NC_ND_RE.search(lic.upper()), f"{row['id']}: NC/ND license {lic!r}"
    assert lic in LICENSE_ALLOWLIST, f"{row['id']}: license {lic!r} not in allowlist"


def test_required_roles_present() -> None:
    missing = REQUIRED_ROLES - {row["role"] for row in ROWS}
    assert not missing, f"no clip for roles: {sorted(missing)}"


@pytest.mark.parametrize("row", ROWS, ids=ROW_IDS)
def test_id_attributed_in_sources(row: dict[str, str]) -> None:
    # Ignore HTML comments so the example row in SOURCES.md doesn't count.
    text = re.sub(r"<!--.*?-->", "", SOURCES.read_text(encoding="utf-8"), flags=re.S)
    assert re.search(rf"(?<![\w-]){re.escape(row['id'])}(?![\w-])", text), (
        f"{row['id']} missing from {SOURCES.name}"
    )


@pytest.mark.parametrize(
    ("value", "flagged"),
    [
        ("CC-BY-NC-4.0", True),
        ("CC-BY-ND-4.0", True),
        ("CC-BY-NC-ND-4.0", True),
        ("CC-BY-SA-4.0", False),
        ("Pexels", False),
        ("PD", False),
    ],
)
def test_nc_nd_detector(value: str, flagged: bool) -> None:
    assert bool(NC_ND_RE.search(value.upper())) is flagged
