"""Regression test for issue #19: wheel-install must ship the
dataset / feature spec markdown so both registries populate.

`Registry.from_repo_specs()` and `FeatureRegistry.from_repo_specs()`
both look for spec markdown in two places:

1. The repo-root `datasets/` / `features/` directories (present in
   editable installs and source checkouts).
2. A wheel-internal mirror under `census_augment/datasets/_specs/` and
   `census_augment/_features/`.

Pre-v1.4.1 the wheel-internal mirrors didn't exist — `pyproject.toml`
shipped only `*.py` files — so a real wheel install came up with both
registries empty. This test builds a wheel from the working tree,
installs it into an isolated subprocess venv, and confirms both
registries populate from the wheel-bundled specs alone.

The build is slow (~10s) so the test is gated on the `WHEEL_E2E=1`
environment variable. CI sets it; local `pytest` runs skip it. The
non-E2E checks below catch the most common regression — pyproject.toml
config drift — without the build cost.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

import pytest

# ---- pyproject config locked down ---------------------------------------


def _pyproject() -> dict:
    """Parse the repo's pyproject.toml, walking up from this file."""
    here = Path(__file__).resolve()
    # tests/ -> repo root
    pyproject_path = here.parents[1] / "pyproject.toml"
    with pyproject_path.open("rb") as fh:
        return tomllib.load(fh)


def test_pyproject_force_includes_dataset_specs() -> None:
    """The hatchling force-include block must map ``datasets/`` into
    the wheel under the path the runtime resolver looks for."""
    cfg = _pyproject()
    fi = cfg["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    assert fi["datasets"] == "census_augment/datasets/_specs", (
        "datasets/ force-include destination changed — runtime resolver in "
        "datasets/_registry.py::_default_spec_dir() expects "
        "<package>/datasets/_specs/."
    )


def test_pyproject_force_includes_feature_specs() -> None:
    """Same lock-down for the feature spec mirror."""
    cfg = _pyproject()
    fi = cfg["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    assert fi["features"] == "census_augment/_features", (
        "features/ force-include destination changed — runtime resolver in "
        "features.py::_default_features_dir() expects <package>/_features/."
    )


# ---- end-to-end wheel-install test (CI) ---------------------------------


@pytest.mark.skipif(
    os.environ.get("WHEEL_E2E") != "1",
    reason="Wheel build + install is slow; set WHEEL_E2E=1 to enable.",
)
def test_wheel_install_populates_both_registries(tmp_path: Path) -> None:
    """Build a wheel, install it into a fresh venv, confirm registries populate.

    Runs in CI to catch regressions where the wheel ships without the
    bundled spec markdown.
    """
    repo_root = Path(__file__).resolve().parents[1]
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()

    # Build the wheel into the test's temp dir so we don't pollute
    # repo_root/dist/.
    subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--outdir",
            str(dist_dir),
            str(repo_root),
        ],
        check=True,
        capture_output=True,
    )
    wheels = list(dist_dir.glob("abs_census_augmentor-*.whl"))
    assert len(wheels) == 1, f"expected exactly one wheel, got {wheels}"
    wheel = wheels[0]

    # Sanity: the wheel itself contains the bundled markdown. If this
    # fails, the install/import step would fail too — but this gives
    # a sharper error if the issue is build-time bundling rather than
    # runtime resolution.
    with zipfile.ZipFile(wheel) as zf:
        names = set(zf.namelist())
    assert any(
        n.startswith("census_augment/datasets/_specs/") and n.endswith(".md") for n in names
    ), "wheel doesn't contain bundled dataset specs"
    assert any(n.startswith("census_augment/_features/") and n.endswith(".md") for n in names), (
        "wheel doesn't contain bundled feature specs"
    )

    # Fresh venv, install only the wheel, run a probe in it.
    venv_dir = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
    if sys.platform == "win32":
        venv_python = venv_dir / "Scripts" / "python.exe"
    else:
        venv_python = venv_dir / "bin" / "python"
    subprocess.run(
        [str(venv_python), "-m", "pip", "install", str(wheel)],
        check=True,
        capture_output=True,
    )

    # Run the probe from a working directory that is NOT the repo
    # root, so any `datasets/`/`features/` shadow at cwd can't
    # accidentally hide the wheel-install gap.
    probe_cwd = tmp_path / "probe-cwd"
    probe_cwd.mkdir()
    probe = subprocess.run(
        [
            str(venv_python),
            "-c",
            (
                "from census_augment.datasets import registry; "
                "from census_augment.features import features; "
                "ds = sorted(s.id for s in registry.list_datasets()); "
                "ft = sorted(s.id for s in features.list_features()); "
                "print('DATASETS=' + ','.join(ds)); "
                "print('FEATURES=' + ','.join(ft))"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=probe_cwd,
    )
    out = probe.stdout

    assert (
        "DATASETS=abs_building_approvals,abs_building_approvals_lga,"
        "abs_business_counts,abs_personal_income,aihw_mh_admitted_patients,"
        "aihw_mh_community,aihw_mh_ed_presentations,aihw_mh_medicare,"
        "aihw_mh_prescriptions,dss_payments,erp_by_sa2,gcp,seifa" in out
    ), f"dataset registry empty or missing entries on wheel install:\n{out}"
    assert (
        "FEATURES=housing_supply_rate,mean_dwelling_approval_value,mh_admitted_avg_length_of_stay,mh_community_contacts_per_patient,mh_medicare_services_per_patient,mh_prescriptions_per_patient,motor_vehicles_per_dwelling,pct_age_pension_recipients,pct_aged_65_plus,pct_apartment_approvals,pct_carer_payment_recipients,pct_commonwealth_rent_assistance_recipients,pct_disability_support_pension_recipients,pct_drive_to_work,pct_employed_full_time,pct_jobseeker_recipients,pct_one_parent_family,pct_parenting_payment_recipients,pct_renters,pct_youth_allowance_recipients,welfare_density_index"
        in out
    ), f"feature registry empty or missing entries on wheel install:\n{out}"

    # Cleanup so subsequent tests don't trip over a half-deleted venv on
    # Windows where antivirus can hold file handles.
    shutil.rmtree(venv_dir, ignore_errors=True)
