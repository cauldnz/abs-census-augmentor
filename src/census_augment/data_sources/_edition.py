"""ASGS-edition boundary file descriptors (spec-temporal.md §13).

Each ABS Statistical Geography Standard "edition" ships boundary files
with different URLs, filenames, datums, and DBF attribute-table column
names. Hard-coding Edition 3 throughout the code worked while we only
supported one edition; landing historical datasets means we need to
parameterise on the edition the dataset release was compiled against
(the spec-temporal.md §2 invariant).

This module holds the per-edition specs and a small factory that
picks the right one for a ``(year, datum)`` pair. The downstream
:class:`census_augment.data_sources.boundaries.BoundariesDataSource`
delegates URL / filename / column-name decisions to the spec rather
than constructing them inline.

Real Data First (CLAUDE.md): the URLs and DBF column names recorded
here were captured via live WebFetch against the ABS landing pages
(see ``tools/verify_real_parsers.py`` ``verify_edition_2_boundaries``).
The verifier is the authoritative drift detector — if ABS retires
the openagent URL pattern or renames a DBF column, that probe is
where the failure surfaces first.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

#: ASGS Edition 3 (July 2021 – June 2026) landing page. The SA2 / MB
#: ZIPs are constructed off this base via ``{filename}`` concatenation.
EDITION_3_BASE_URL = (
    "https://www.abs.gov.au/statistics/standards/"
    "australian-statistical-geography-standard-asgs-edition-3/"
    "jul2021-jun2026/access-and-downloads/digital-boundary-files"
)

#: Edition 2 SA2 shapefile download URL — full URL including the ABS
#: Lotus Notes "openagent" logging query string. Edition 2 predates the
#: tidy Edition-3 base-url-plus-filename pattern; the UNID hex hash
#: (``A09309ACB3FA50B8CA257FED0013D420``) is the SA2-2016 page entry
#: in the ABS subscriber DB and is stable for as long as ABS hosts
#: this catalogue page. Captured via WebFetch 2026-05-19.
_EDITION_2_SA2_URL = (
    "https://www.ausstats.abs.gov.au/ausstats/subscriber.nsf/log"
    "?openagent&1270055001_sa2_2016_aust_shape.zip&1270.0.55.001"
    "&Data%20Cubes&A09309ACB3FA50B8CA257FED0013D420&0"
    "&July%202016&12.07.2016&Latest"
)

#: Edition 1 SA2 shapefile download URL — same Lotus Notes "openagent"
#: form as Edition 2. The UNID hex hash
#: (``7130A5514535C5FCCA257801000D3FBD``) is the SA2-2011 page entry
#: in the ABS subscriber DB, captured via WebFetch 2026-05-29 against
#: the live 1270.0.55.001 (ASGS Vol 1) July 2011 details page.
_EDITION_1_SA2_URL = (
    "https://www.abs.gov.au/ausstats/subscriber.nsf/log"
    "?openagent&1270055001_sa2_2011_aust_shape.zip&1270.0.55.001"
    "&Data%20Cubes&7130A5514535C5FCCA257801000D3FBD&0"
    "&July%202011&23.12.2010&Latest"
)

#: Datum literal — only the two ABS currently publishes.
Datum = Literal["GDA94", "GDA2020"]


@dataclass(frozen=True)
class BoundaryEditionSpec:
    """One ABS ASGS edition's boundary-file descriptor.

    Fields:

    - ``edition`` — 2 (Jul 2016 – Jun 2021) or 3 (Jul 2021 – Jun 2026).
    - ``year`` — the boundary year embedded in filenames (2016 for
      Edition 2, 2021 for Edition 3).
    - ``datum`` — geodetic datum. Edition 2 ships GDA94 only;
      Edition 3 ships both GDA2020 (default) and GDA94.
    - ``sa2_zip_filename`` — the bare ZIP filename ABS publishes.
      Used as the on-disk cache file name and as the extract-dir name.
    - ``sa2_download_url`` — full HTTPS URL for the SA2 ZIP download.
      Differs in shape between editions (Edition 3 is base-URL +
      filename; Edition 2 is a Lotus Notes openagent query string).
    - ``sa2_code_column`` / ``sa2_name_column`` — DBF attribute-table
      column names. Edition 2 uses ``SA2_MAIN16`` / ``SA2_NAME16``;
      Edition 3 uses ``SA2_CODE21`` / ``SA2_NAME21``.

    Mesh Block (MB) Edition 2 isn't represented yet because ABS
    publishes Edition 2 MB shapefiles per state/territory, not
    nationally — wiring the 8-file concat is Phase F follow-up
    work. Edition 3 keeps a single national MB ZIP which the existing
    :class:`MbCorrespondenceDataSource` handles directly.
    """

    edition: Literal[1, 2, 3]
    year: Literal[2011, 2016, 2021]
    datum: Datum
    sa2_zip_filename: str
    sa2_download_url: str
    sa2_code_column: str
    sa2_name_column: str


def edition_3_spec(
    *,
    datum: Datum = "GDA2020",
    base_url: str = EDITION_3_BASE_URL,
) -> BoundaryEditionSpec:
    """Return the spec for ASGS Edition 3 (current Census, Jul 2021+).

    ``base_url`` is overridable so test mocks (and the existing
    ``DataSourcesConfig.boundaries_base_url`` knob) can swap in a
    different host. Default points at the live ABS landing page.
    """
    filename = f"SA2_2021_AUST_SHP_{datum}.zip"
    return BoundaryEditionSpec(
        edition=3,
        year=2021,
        datum=datum,
        sa2_zip_filename=filename,
        sa2_download_url=f"{base_url.rstrip('/')}/{filename}",
        sa2_code_column="SA2_CODE21",
        sa2_name_column="SA2_NAME21",
    )


def edition_2_spec() -> BoundaryEditionSpec:
    """Return the spec for ASGS Edition 2 (Jul 2016 – Jun 2021).

    Edition 2 only ships in GDA94 — ABS hadn't published GDA2020
    boundaries yet at the 2016 Census. The download URL is the
    Lotus Notes "openagent" form ABS used pre-2020; it's not
    parametrisable in the same way as Edition 3, so we hard-code it.
    """
    return BoundaryEditionSpec(
        edition=2,
        year=2016,
        datum="GDA94",
        sa2_zip_filename="1270055001_sa2_2016_aust_shape.zip",
        sa2_download_url=_EDITION_2_SA2_URL,
        sa2_code_column="SA2_MAIN16",
        sa2_name_column="SA2_NAME16",
    )


def edition_1_spec() -> BoundaryEditionSpec:
    """Return the spec for ASGS Edition 1 (Jul 2011 – Jun 2016).

    The first ASGS edition; same Lotus Notes openagent URL form as
    Edition 2. GDA94 only (GDA2020 didn't exist yet). DBF columns use
    the ``11`` year-suffix convention (``SA2_MAIN11`` / ``SA2_NAME11``).

    Live-probed 2026-05-29: 2,214 SA2 polygons, CRS GDA94 / EPSG:4283,
    9-digit SA2 codes matching SEIFA 2011's ``2011 Statistical Area
    Level 2 Code (SA2)`` column.
    """
    return BoundaryEditionSpec(
        edition=1,
        year=2011,
        datum="GDA94",
        sa2_zip_filename="1270055001_sa2_2011_aust_shape.zip",
        sa2_download_url=_EDITION_1_SA2_URL,
        sa2_code_column="SA2_MAIN11",
        sa2_name_column="SA2_NAME11",
    )


def edition_spec_for(
    *,
    year: Literal[2011, 2016, 2021],
    datum: Datum,
    base_url: str | None = None,
) -> BoundaryEditionSpec:
    """Pick the boundary edition spec matching ``(year, datum)``.

    ``base_url`` only affects Edition 3 (where the URL is base + filename);
    Editions 1 and 2 use the fixed ABS openagent URL form. Raises
    :class:`ValueError` for ``(2011, "GDA2020")`` and
    ``(2016, "GDA2020")`` — neither ABS release exists in GDA2020.
    """
    if year == 2011:
        if datum != "GDA94":
            raise ValueError(
                f"ASGS Edition 1 (2011 boundaries) is only published in GDA94; "
                f"got datum={datum!r}. Set census.datum=GDA94 or use a more "
                f"recent census.year for GDA2020 boundaries."
            )
        return edition_1_spec()
    if year == 2016:
        if datum != "GDA94":
            raise ValueError(
                f"ASGS Edition 2 (2016 boundaries) is only published in GDA94; "
                f"got datum={datum!r}. Set census.datum=GDA94 or use "
                f"census.year=2021 for GDA2020 boundaries."
            )
        return edition_2_spec()
    if year == 2021:
        if base_url is None:
            return edition_3_spec(datum=datum)
        return edition_3_spec(datum=datum, base_url=base_url)
    raise ValueError(  # pragma: no cover — Literal typing catches this at type-check time
        f"Unsupported boundary year {year!r}; expected 2011, 2016 or 2021."
    )
