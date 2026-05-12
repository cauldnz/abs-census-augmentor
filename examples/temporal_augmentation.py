"""Temporal-mode augmentation (v1.5).

Demonstrates the headline use case from `spec-temporal.md`: each input
row picks the dataset snapshot closest to its timestamp, with the
spatial lookup happening at the boundary edition the dataset release
was actually compiled against (see §2 of `spec-temporal.md`).

This example uses simulated 5-year transaction data at a fixed
Sydney CBD location and asks for ERP population for each row. The
output column ``erp_by_sa2_release`` will name which ERP release
each row resolved to.

Run with::

    python examples/temporal_augmentation.py

First run downloads ~90 MB of ABS data into the user cache; subsequent
runs are instant.
"""

from __future__ import annotations

import pandas as pd

from census_augment import Pipeline


def main() -> None:
    # Five years of monthly transactions at the same lat/lon — but
    # crucially with timestamps spanning multiple ABS release windows.
    # The pipeline will resolve each row's ERP release independently.
    df = pd.DataFrame(
        {
            "label": [
                "Sydney CBD — 2021",
                "Sydney CBD — 2022",
                "Sydney CBD — 2023",
                "Sydney CBD — 2024",
            ],
            "lat": [-33.8568, -33.8568, -33.8568, -33.8568],
            "lon": [151.2153, 151.2153, 151.2153, 151.2153],
            # All dates are post-mid-2021 (ASGS Edition 3) so this
            # single-edition example runs without Phase F's
            # cross-edition support.
            "transaction_date": pd.to_datetime(
                ["2022-03-15", "2023-03-15", "2024-03-15", "2024-09-15"]
            ),
        }
    )

    pipeline = Pipeline.create(
        variables={
            "population": "ERP.population_total",
        },
        user_agent="my-app/1.0 (me@example.com)",
        latitude_column="lat",
        longitude_column="lon",
        date_column="transaction_date",  # enables temporal mode
    )

    result = pipeline.augment(df)

    print("=== Output ===")
    print(
        result.df[
            ["label", "transaction_date", "sa2_code", "erp_by_sa2_release", "sa2_population"]
        ].to_string()
    )
    print()
    print("=== Releases used (per-dataset) ===")
    print(result.releases_used)
    print()
    print(
        "Note: each row's `erp_by_sa2_release` reflects the ERP snapshot closest\n"
        "to that row's transaction date. The pipeline did one ERP load per\n"
        "distinct release referenced (per `result.releases_used`)."
    )


if __name__ == "__main__":
    main()
