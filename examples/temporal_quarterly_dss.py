"""Temporal mode with a quarterly-cadence dataset (DSS payments).

Shows the difference between the two resolution rules
(`closest_at_or_before` vs `closest`) for granular cadence datasets.
With quarterly DSS, monthly-input transactions can fall awkwardly
on the wrong side of a quarter-end — `closest` typically does what
the user expects, `closest_at_or_before` is causally correct for
"what was the DSS snapshot in force at this date" semantics.

Run with::

    python examples/temporal_quarterly_dss.py
"""

from __future__ import annotations

import pandas as pd

from census_augment import Pipeline


def main() -> None:
    # Six monthly transactions, all in calendar 2024, all on
    # ASGS Edition 3.
    df = pd.DataFrame(
        {
            "label": [f"2024-{m:02d}-15" for m in range(4, 10)],
            "lat": [-33.8568] * 6,
            "lon": [151.2153] * 6,
            "transaction_date": pd.to_datetime([f"2024-{m:02d}-15" for m in range(4, 10)]),
        }
    )

    # Configuration A — default `closest_at_or_before`.
    pipeline_default = Pipeline.create(
        variables={"age_pension": "DSS.age_pension_recipients"},
        user_agent="my-app/1.0 (me@example.com)",
        latitude_column="lat",
        longitude_column="lon",
        date_column="transaction_date",
    )
    print("=== closest_at_or_before (default) ===")
    result_a = pipeline_default.augment(df)
    print(result_a.df[["label", "transaction_date", "dss_payments_release"]].to_string())
    print()

    # Configuration B — `closest` per-dataset.
    from census_augment.config import (
        Config,
        InputConfig,
        OutputConfig,
        CensusConfig,
        DataSourcesConfig,
        GeocodingConfig,
        NominatimConfig,
        TemporalConfig,
        TemporalResolutionConfig,
    )

    config = Config(
        input=InputConfig(
            latitude_column="lat",
            longitude_column="lon",
            date_column="transaction_date",
        ),
        output=OutputConfig(prefix="sa2_"),
        census=CensusConfig(),
        data_sources=DataSourcesConfig(),
        geocoding=GeocodingConfig(
            providers=["nominatim"],
            nominatim=NominatimConfig(user_agent="my-app/1.0 (me@example.com)"),
        ),
        variables={"age_pension": "DSS.age_pension_recipients"},
        temporal=TemporalConfig(
            per_dataset={
                "dss_payments": TemporalResolutionConfig(resolution="closest"),
            }
        ),
    )
    pipeline_closest = Pipeline.from_config(config)
    print("=== closest (per-dataset override) ===")
    result_b = pipeline_closest.augment(df)
    print(result_b.df[["label", "transaction_date", "dss_payments_release"]].to_string())
    print()
    print(
        "Note: a 2024-04-15 row picks DSS Q2 2024 under `closest` (the\n"
        "quarter whose midpoint is nearest), but `closest_at_or_before`\n"
        "picks Q1 2024 (the most recent already-published quarter as of\n"
        "the transaction date). Both are correct under their respective\n"
        "semantics; pick whichever matches your causality assumptions."
    )


if __name__ == "__main__":
    main()
