"""Library use with PRESET features (v1.4).

PRESET features are curated ratios (``pct_renters``, ``pct_drive_to_work``,
``pct_aged_65_plus``, etc.) that combine multiple GCP variables with the
right denominator pre-baked. v1.3 shipped them as a standalone
``FeatureEvaluator``; v1.4 wires ``PRESET.<id>`` into the pipeline as
a first-class variable namespace alongside ``G\\d+.<col>``,
``SEIFA.<field>``, ``ERP.<field>``, ``DSS.<field>``, and ``ATO.<field>``.

You no longer need to request the underlying GCP source columns yourself
or apply ``FeatureEvaluator`` manually — the pipeline auto-loads the
numerator + denominator inputs (deduplicated across PRESETs) and
surfaces the derived column under the configured output prefix.

Run with::

    python examples/library_with_preset_features.py

Output: a short DataFrame whose rows are the input addresses and
whose enrichment columns are the PRESETs the config asked for.

For the standalone-evaluator workflow against an already-built
SA2-keyed DataFrame (notebook exploration, custom workflows that don't
need geocoding), see :class:`census_augment.features.FeatureEvaluator`.
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

    # Mix GCP, SEIFA, and PRESET variables in one config — the pipeline
    # dispatches each through the right loader. The PRESETs' source
    # columns (G37.R_Tot, G37.OPDs_Total, G62.* for drive-to-work, etc.)
    # are loaded transparently from GCP and dropped from the output.
    pipeline = Pipeline.create(
        latitude_column="lat",
        longitude_column="lon",
        variables={
            "median_income": "G02.Median_tot_hhd_inc_weekly",
            "renters_pct": "PRESET.pct_renters",
            "drove_pct": "PRESET.pct_drive_to_work",
            "aged_65_pct": "PRESET.pct_aged_65_plus",
            "irsd_decile": "SEIFA.irsd_aus_decile",
        },
        user_agent="census-augment-example/1.4 (someone@example.com)",
    )

    result = pipeline.augment(df)
    print(
        result.df[
            [
                "label",
                "sa2_name",
                "sa2_median_income",
                "sa2_renters_pct",
                "sa2_drove_pct",
                "sa2_aged_65_pct",
                "sa2_irsd_decile",
            ]
        ].to_string(index=False)
    )


if __name__ == "__main__":
    main()
