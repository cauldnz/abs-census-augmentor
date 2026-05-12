"""Standalone dataset fetcher use (v1.3).

The new dataset fetchers under :mod:`census_augment.datasets` are
usable directly without the full Pipeline. Useful when you want
SA2-keyed data for analysis but don't need geocoding / spatial-join
on top.

This example demonstrates each of the four new datasets in v1.3:
SEIFA, ERP, DSS, and ABS Personal Income (formerly mislabelled
ATO). Each call hits the upstream source on
first run (one-time download) and caches a parquet locally for
subsequent calls.

Run with:
    python examples/standalone_dataset_fetchers.py

Output: shape + sample rows for each dataset.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from census_augment.datasets._abs_pia import AbsPiaDataSource
from census_augment.datasets._dss import DssDataSource
from census_augment.datasets._erp import ErpDataSource
from census_augment.datasets._seifa import SeifaDataSource


def main() -> None:
    # tmp dir for the demo; in real use you'd point at a stable cache
    # like ~/.cache/census-augment/data/.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        seifa = SeifaDataSource(root=root / "seifa")
        df_seifa = seifa.load()
        print(f"SEIFA 2021: {len(df_seifa):,} SA2s × {len(df_seifa.columns)} cols")
        print(df_seifa.head(2).to_string())
        print()

        erp = ErpDataSource(root=root / "erp")
        df_erp = erp.load()
        print(
            f"ERP {erp.resolved_release}: {len(df_erp):,} SA2s × "
            f"{len(df_erp.columns)} cols (latest reference year: "
            f"{df_erp['reference_year'].iloc[0]})"
        )
        print(df_erp[["population_total", "state_abbreviation"]].head(2).to_string())
        print()

        dss = DssDataSource(root=root / "dss")
        df_dss = dss.load()
        print(f"DSS {dss.resolved_release}: {len(df_dss):,} SA2s × {len(df_dss.columns)} cols")
        # Show a couple of payment-type columns.
        cols = [c for c in df_dss.columns if "age_pension" in c or "jobseeker" in c][:2]
        print(df_dss[cols].head(2).to_string())
        print()

        abs_pia = AbsPiaDataSource(root=root / "abs_personal_income")
        df_pia = abs_pia.load()
        print(
            f"ABS Personal Income {abs_pia.resolved_release}: "
            f"{len(df_pia):,} SA2s × {len(df_pia.columns)} cols"
        )
        print(
            df_pia[["median_total_income", "mean_total_income", "income_earners_count"]]
            .head(2)
            .to_string()
        )


if __name__ == "__main__":
    main()
