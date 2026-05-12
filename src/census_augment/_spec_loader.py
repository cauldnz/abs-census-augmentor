"""Shared loader for the markdown-spec discovery pattern.

Both the dataset registry (``datasets/_registry.py::Registry.from_repo_specs``)
and the feature registry (``features.py::FeatureRegistry.from_repo_specs``)
walk a directory of ``*.md`` files, skip leading-underscore filenames
(reserved for templates / docs), and parse each into a spec dataclass.
The loop was duplicated between the two; this module pulls it out.

The parser callback can fail with ``ValueError`` on a malformed spec;
the loader logs+skips and continues, matching the existing behaviour
of both registries.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TypeVar

_log = logging.getLogger(__name__)

T = TypeVar("T")


def iter_specs_from_dir(
    directory: Path,
    parser: Callable[[Path], T],
    *,
    label: str = "spec",
) -> Iterator[T]:
    """Yield parsed specs from every ``*.md`` file under ``directory``.

    Skips leading-underscore filenames (``_template.md``, etc.) and
    silently swallows ``ValueError`` from ``parser`` with a logged
    warning — that's the behaviour both ``Registry.from_repo_specs``
    and ``FeatureRegistry.from_repo_specs`` had inline, so preserving
    it keeps semantics identical.

    ``label`` is woven into the warning so the log line names which
    registry skipped the file.
    """
    if not directory.is_dir():
        _log.debug("No %s files found at %s", label, directory)
        return
    for spec_path in sorted(directory.glob("*.md")):
        if spec_path.name.startswith("_"):
            continue
        try:
            yield parser(spec_path)
        except ValueError:
            _log.exception("Skipping invalid %s at %s", label, spec_path)
            continue
