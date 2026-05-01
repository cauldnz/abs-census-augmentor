"""Minimal library use of census-augment.

Run this script directly:

    python examples/library_basic.py

First run downloads ~90 MB of ABS data into the user cache (one-off).
Subsequent runs are instant (cached).

What it shows
-------------
1.  ``Pipeline.create(...)`` — notebook-friendly factory, no YAML needed.
2.  ``pipeline.augment(df)`` — DataFrame in / DataFrame out.
3.  Using ``AugmentResult`` to inspect summary stats and filter
    fully-enriched rows.
"""

from __future__ import annotations

import pandas as pd

from census_augment import Pipeline


def main() -> None:
    pipeline = Pipeline.create(
        variables={
            "median_age": "G02.Median_age_persons",
            "median_household_income_weekly": "G02.Median_tot_hhd_inc_weekly",
            "total_population": "G01.Tot_P_P",
        },
        user_agent="census-augment-example/0.1 (someone@example.com)",
        latitude_column="lat",
        longitude_column="lon",
    )

    df = pd.DataFrame(
        {
            "label": [
                "Sydney Opera House",
                "Melbourne MCG",
                "Brisbane CBD",
                "Open ocean",
            ],
            "lat": [-33.8568, -37.8200, -27.4698, -35.0],
            "lon": [151.2153, 144.9831, 153.0251, 155.0],
        }
    )

    result = pipeline.augment(df)

    print("=== Run summary ===")
    print(result.summary.format_human_readable())

    print("=== Augmented DataFrame ===")
    print(result.df.to_string())
    print()

    print("=== Fully-enriched rows only ===")
    print(result.df[result.is_fully_enriched].to_string())


if __name__ == "__main__":
    main()
