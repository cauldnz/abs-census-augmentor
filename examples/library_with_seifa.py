"""Library use with SEIFA enrichment (v1.3).

Demonstrates the new pluggable-dataset framework: a single
``Pipeline.augment(df)`` call resolves both GCP variables and SEIFA
variables, dispatched through the registry to their respective
fetchers.

The first call downloads the SEIFA SA2 XLSX from ABS (~150 KB) and
caches it under the user data dir. Subsequent calls re-use the
cache.

Run with:
    python examples/library_with_seifa.py

Output: a short table of the input addresses with their SA2 codes,
the IRSD score / decile, and the median household income from the
GCP DataPack.
"""

from __future__ import annotations

import pandas as pd

from census_augment import Pipeline


def main() -> None:
    df = pd.DataFrame(
        {
            "label": [
                "Sydney Opera House",
                "Bondi Beach",
                "Adelaide Central Market",
            ],
            "lat": [-33.8568, -33.8908, -34.9290],
            "lon": [151.2153, 151.2743, 138.5990],
        }
    )

    # Mix GCP and SEIFA variables in one config — both routes work
    # transparently. The Pipeline dispatches each through the right
    # dataset's fetcher.
    pipeline = Pipeline.create(
        latitude_column="lat",
        longitude_column="lon",
        variables={
            "median_age": "G02.Median_age_persons",
            "median_income": "G02.Median_tot_hhd_inc_weekly",
            "irsd_score": "SEIFA.irsd_score",
            "irsd_decile": "SEIFA.irsd_aus_decile",
            "ieo_decile": "SEIFA.ieo_aus_decile",
        },
        user_agent="census-augment-example/1.3 (someone@example.com)",
    )

    result = pipeline.augment(df)
    print(
        result.df[
            [
                "label",
                "sa2_name",
                "sa2_median_age",
                "sa2_median_income",
                "sa2_irsd_score",
                "sa2_irsd_decile",
                "sa2_ieo_decile",
            ]
        ].to_string(index=False)
    )


if __name__ == "__main__":
    main()
