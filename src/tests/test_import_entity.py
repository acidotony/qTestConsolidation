"""Inject one exported artifact into the target instance.

The second half of the migration, at its smallest: read what the export
wrote, create it in HCSC, record the id the target gave it, move its file to
`migration/imported/` -- which is what says it crossed -- and capture what
HCSC shows for it.

    pytest src/tests/test_import_entity.py -s

Which artifacts to inject is data, not code: `src/tests/input/import_entity.json`
lists them, and the test runs once per entry. Each one must already have been
exported -- run `test_export_entity.py` first, or the test says which file is
missing.

Skipped until the target instance is configured, which it is not while the
proof-of-concept project is still being created.
"""

import json
import shutil
from pathlib import Path

import pytest

from main.artifacts import standalone_file_name
from main.client import WebClient
from main.importer import CREATABLE_FIELDS, DataImporter

INPUT_FILE = Path(__file__).resolve().parent / "input" / "import_entity.json"


def artifacts_to_import():
    """The artifacts listed in the input file, read at collection time."""
    if not INPUT_FILE.is_file():
        return []
    return json.loads(INPUT_FILE.read_text(encoding="utf-8")).get("artifacts", [])


@pytest.fixture(
    params=artifacts_to_import(),
    ids=lambda entry: f"{entry['artifact_type']}-{entry['id']}",
)
def artifact_to_import(request):
    """One entry of the input file, feeding a run of the test."""
    return request.param


@pytest.fixture(scope="session")
def target_browser():
    """A browser on the HCSC side, closed once the injections are done."""
    with WebClient.from_env("target") as client:
        yield client


@pytest.mark.integration
def test_import_artifact_on_its_own(
    target_client,
    target_project_id,
    target_browser,
    artifact_to_import,
):
    """The exported artifact is created in HCSC, recorded, filed and captured."""
    artifact_type = artifact_to_import["artifact_type"]
    source_id = artifact_to_import["id"]

    importer = DataImporter(target_client, web_client=target_browser)
    kind = importer.resolve_artifact_type(artifact_type)
    file_name = f"{standalone_file_name(kind, source_id)}.json"
    exported_file = importer.exported_dir / file_name

    if not exported_file.is_file():
        pytest.skip(f"{exported_file} does not exist: export the {kind} first")

    exported = json.loads(exported_file.read_text(encoding="utf-8"))
    created = importer.import_standalone(artifact_type, source_id, target_project_id)

    assert created.get("id"), "The target returned no id"
    assert created.get("name") == exported.get("name"), "The target renamed the artifact"

    # Which folder holds the file is the record of where the artifact got to.
    assert not exported_file.exists(), "The exported file was left behind"
    assert (importer.imported_dir / file_name).is_file(), "The file did not reach imported/"

    recorded = importer.target_id_of(artifact_type, source_id)
    assert recorded == created["id"], "The id map does not point at what was created"

    capture = target_browser.capture_path(artifact_type, source_id)
    print(f"\nImported {kind} {source_id} -> {created['id']} in {target_client.base_url}")
    print(f"  name         : {created.get('name')}")
    print(f"  carried over : {sorted(CREATABLE_FIELDS[kind])}")
    print(f"  filed as     : {importer.imported_dir / file_name}")
    print(f"  id map       : {importer.id_map_file}")
    print(f"  capture      : {capture if capture.is_file() else 'none taken, see the log'}")


@pytest.mark.integration
def test_importing_again_replaces_rather_than_duplicates(
    target_client,
    target_project_id,
    artifact_to_import,
):
    """A second run of the same artifact leaves one copy in the target, not two."""
    artifact_type = artifact_to_import["artifact_type"]
    source_id = artifact_to_import["id"]

    importer = DataImporter(target_client)
    kind = importer.resolve_artifact_type(artifact_type)
    file_name = f"{standalone_file_name(kind, source_id)}.json"
    already_imported = importer.imported_dir / file_name

    if not already_imported.is_file():
        pytest.skip(f"{kind} {source_id} has not been imported yet")

    first_id = importer.target_id_of(artifact_type, source_id)
    # Put the file back where an import reads from, as re-exporting would.
    shutil.copy2(already_imported, importer.exported_dir / file_name)

    created = importer.import_standalone(artifact_type, source_id, target_project_id)
    name = created.get("name")

    surviving = importer.find_by_name(kind, name, target_project_id)
    assert len(surviving) == 1, f"The target holds {len(surviving)} {kind}s named {name!r}"
    assert surviving[0]["id"] == created["id"], "The surviving copy is not the new one"
    assert importer.target_id_of(artifact_type, source_id) == created["id"]

    print(f"\nRe-imported {kind} {source_id}: {first_id} deleted, {created['id']} created")
