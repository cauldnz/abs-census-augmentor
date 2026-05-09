"""Library use with PRESET features (v1.3).

PRESET features are curated ratios (pct_renters, pct_drive_to_work,
etc.) that combine multiple GCP variables with the right denominator
pre-baked. v1.3 ships the FeatureEvaluator as a standalone API; full
pipeline integration lands in v1.4.

This example shows how to use the evaluator manually:

1. Build a SA2-keyed DataFrame containing the source columns the
   PRESET references (e.g. ``G37.R_Tot`` and ``G37.OPDs_Total`` for
   ``pct_renters``).
2. Look up the PRESET spec via the package-level features registry.
3. Run :class:`FeatureEvaluator` to compute the derived column.

For the v1.3 pipeline-integrated path you can request the underlying
GCP fields and apply the PRESET formula yourself; we'll automate this
in v1.4.

Run with:
    python examples/library_with_preset_features.py

Output: a small DataFrame with the four GCP source columns plus
the computed pct_renters and pct_aged_65_plus features.
"""

from __future__ import annotations

import pandas as pd

from census_augment.features import FeatureEvaluator, features


def main() -> None:
    # Synthetic SA2-keyed DataFrame matching the inputs the two
    # PRESETs need.
    df = pd.DataFrame(
        {
            # pct_renters source columns
            "G37.R_Tot": [4500, 250, 1200],
            "G37.OPDs_Total": [9000, 1000, 8000],
            # pct_aged_65_plus source columns
            "G04.Age_65_yr_above_P": [800, 150, 1800],
            "G01.Tot_P_P": [12000, 2000, 9500],
        },
        index=pd.Index(
            ["117011326", "117011327", "117011328"], name="sa2_code_2021"
        ),
    )

    # Pick PRESETs from the registry.
    pct_renters_spec = features.get("pct_renters")
    pct_aged_65_plus_spec = features.get("pct_aged_65_plus")

    df["pct_renters"] = FeatureEvaluator(pct_renters_spec).evaluate(df)
    df["pct_aged_65_plus"] = FeatureEvaluator(pct_aged_65_plus_spec).evaluate(df)

    print(
        df[
            [
                "G37.R_Tot",
                "G37.OPDs_Total",
                "pct_renters",
                "G04.Age_65_yr_above_P",
                "G01.Tot_P_P",
                "pct_aged_65_plus",
            ]
        ].to_string()
    )


if __name__ == "__main__":
    main()
