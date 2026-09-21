"""Export one artifact of the source instance on its own.

The first half of the migration, at its smallest: read a single artifact,
write it to `migration/exported/`, and capture what HS shows for it. What
comes out is what the injection will read.

    pytest src/tests/test_export_entity.py -s

Which artifacts to export is data, not code: `src/tests/input/export_entity.json`
lists them, and the test runs once per entry. Add an entry there to export
another artifact -- of any kind the exporter knows, not only requirements.

Skipped when `config/qtest.env` has no credentials, like every test that
talks to a live instance. The capture is skipped on its own terms when the
browser settings are absent, and the export still counts.
"""

import json
from pathlib import Path

import pytest

from main.artifacts import STANDALONE_SUFFIX
from main.client import WebClient
from main.exporter import DataExporter

INPUT_FILE = Path(__file__).resolve().parent / "input" / "export_entity.json"


def artifacts_to_export():
    """The artifacts listed in the input file, read at collection time."""
    if not INPUT_FILE.is_file():
        return []
    return json.loads(INPUT_FILE.read_text(encoding="utf-8")).get("artifacts", [])


@pytest.fixture(
    params=artifacts_to_export(),
    ids=lambda entry: f"{entry['artifact_type']}-{entry['id']}",
)
def artifact_to_export(request):
    """One entry of the input file, feeding a run of the test."""
    return request.param


@pytest.fixture(scope="session")
def source_browser():
    """A browser on the HS side, closed once the exports are done."""
    with WebClient.from_env("source") as client:
        yield client


@pytest.mark.integration
def test_export_artifact_on_its_own(
    source_client,
    project_id,
    source_browser,
    artifact_to_export,
):
    """The artifact comes back from HS, lands in migration/exported, and is captured."""
    artifact_type = artifact_to_export["artifact_type"]
    artifact_id = artifact_to_export["id"]

    exporter = DataExporter(source_client, web_client=source_browser)
    written = exporter.export_standalone(artifact_type, artifact_id, project_id)
    artifact = json.loads(written.read_text(encoding="utf-8"))

    assert int(artifact["id"]) == int(artifact_id), "The response is for another artifact"
    assert artifact.get("name"), "The artifact came back with no name"

    assert written.is_file(), f"{written} was not written"
    assert written.name.endswith(f"_{artifact_id}_{STANDALONE_SUFFIX}.json")

    capture = source_browser.capture_path(artifact_type, artifact_id)
    print(f"\nExported {artifact_type} {artifact_id} -> {written}")
    print(f"  name    : {artifact['name']}")
    print(f"  fields  : {sorted(artifact)}")
    print(f"  capture : {capture if capture.is_file() else 'none taken, see the log'}")

    expected = artifact_to_export.get("name")
    if expected and artifact["name"] != expected:
        print(f"  NOTE: named {artifact['name']!r} in HS, {expected!r} in the input file")
