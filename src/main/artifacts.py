"""The vocabulary of qTest artifacts, shared by the export and the injection.

Both sides need to agree on two things: what an artifact kind is called, and
what the file holding one is named. The exporter writes that file and the
importer reads it, so the convention lives here rather than in either of them.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

#: Every artifact kind the migration handles, by the name it is called.
ARTIFACT_TYPES = (
    "requirement",
    "test-case",
    "test-run",
    "test-cycle",
    "test-suite",
    "release",
    "module",
)

#: Suffix of the file holding one artifact read on its own.
STANDALONE_SUFFIX = "stand-alone"


class UnknownArtifactError(RuntimeError):
    """Raised when an artifact kind is not one the migration handles."""


def resolve_artifact_type(artifact_type: str) -> str:
    """The known artifact kind behind whatever spelling was given.

    Singular or plural, hyphens, underscores or camelCase all resolve, so
    callers -- and the input files they read -- are not held to one spelling.
    """
    # Split camelCase first: once lowercased there is no case left to read.
    spelled = re.sub(r"(?<=[a-z])(?=[A-Z])", "-", str(artifact_type or "").strip())
    wanted = re.sub(r"[\s_]+", "-", spelled).lower().rstrip("s")

    for kind in ARTIFACT_TYPES:
        if wanted == kind or wanted == kind.rstrip("s"):
            return kind

    logger.error("Unknown artifact type %r", artifact_type)
    raise UnknownArtifactError(
        f"Unknown artifact type {artifact_type!r}, expected one of {sorted(ARTIFACT_TYPES)}"
    )


def file_label(kind: str) -> str:
    """The artifact kind as it reads in a file name: "test-case" -> TestCase."""
    return "".join(part.capitalize() for part in kind.split("-"))


def standalone_file_name(kind: str, artifact_id: object) -> str:
    """Name of the file holding one artifact, without its extension.

    The name is derived from the kind and the id alone, so the injection can
    find what the export wrote without an index between them.
    """
    return f"{file_label(kind)}_{artifact_id}_{STANDALONE_SUFFIX}"
