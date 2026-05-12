"""Library use with column-name overrides + custom prefix + mask filtering.

Run this script directly:

    python examples/library_with_overrides.py

What it shows
-------------
1.  Constructing a Pipeline whose configured column names don't match the
    DataFrame at hand (a real notebook situation).
2.  Per-call ``address_column=`` / ``latitude_column=`` / ``longitude_column=``
    overrides on ``augment(df)``.
3.  A custom ``output_prefix`` so the new columns don't clash with your
    existing schema.
4.  Using ``AugmentResult.added_columns`` to select just the columns this
    run produced.
5.  Using the per-row classification masks (``is_fully_enriched``,
    ``geocoding_failed``, ``sa2_unmatched``) for filtering and reporting.
"""

from __future__ import annotations

import pandas as pd

from census_augment import Pipeline


def main() -> None:
    pipeline = Pipeline.create(
        variables={
            "median_age": "G02.Median_age_persons",
            "population": "G01.Tot_P_P",
        },
        user_agent="census-augment-example/0.1 (someone@example.com)",
        # Configure with the "default" column names. We'll override per call.
        latitude_column="latitude",
        longitude_column="longitude",
        output_prefix="abs_2021_",
    )

    # A made-up notebook DataFrame with non-default column names.
    df = pd.DataFrame(
        {
            "site_id": [1, 2, 3, 4],
            "name": ["Sydney CBD", "Melbourne CBD", "Open ocean", "Bad row"],
            # Sydney CBD: avoid lat/lon points that fall in Sydney Harbour
            # itself (water has no SA2 — see "Open ocean" row for that case).
            "lat_dec_deg": [-33.8688, -37.81, -35.0, None],
            "lon_dec_deg": [151.2093, 144.96, 155.0, None],
        }
    )

    # Override the column names for THIS call only — config stays unchanged.
    result = pipeline.augment(
        df,
        latitude_column="lat_dec_deg",
        longitude_column="lon_dec_deg",
    )

    print("=== Columns this augment added ===")
    print(result.added_columns)
    print()

    print("=== Original IDs alongside the new enrichment columns ===")
    print(result.df[["site_id", "name", *result.added_columns]].to_string())
    print()

    print("=== Per-row classification ===")
    classification = pd.DataFrame(
        {
            "name": result.df["name"],
            "fully_enriched": result.is_fully_enriched,
            "sa2_unmatched": result.sa2_unmatched,
            "geocoding_failed": result.geocoding_failed,
        }
    )
    print(classification.to_string())


if __name__ == "__main__":
    main()
