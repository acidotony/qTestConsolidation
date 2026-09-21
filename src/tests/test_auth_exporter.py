"""Integration test for the `AuthExporter`.

Runs only when `config/qtest.env` provides credentials; otherwise skipped.
"""

import json
from pathlib import Path

import pytest

from main.exporter import AuthExporter


@pytest.mark.integration
def test_auth_exporter_writes_users_roles_permissions(source_client, project_id):
    """Fetch auth entities and assert files were written.

    The assertions are permissive: some instances may not expose a
    permissions endpoint and that is tolerated.
    """
    exporter = AuthExporter(source_client)
    written = exporter.export_all()

    # Users and roles should be present
    assert "users_json" in written and written["users_json"].is_file()
    assert "users_csv" in written and written["users_csv"].is_file()

    assert "roles_json" in written and written["roles_json"].is_file()
    assert "roles_csv" in written and written["roles_csv"].is_file()

    # Permissions may be absent on some qTest editions; when present they
    # must be written as well.
    if "permissions_json" in written:
        assert written["permissions_json"].is_file()
        assert written["permissions_csv"].is_file()

    # Sanity-check: users CSV has at least a header and optionally rows.
    users_csv = written["users_csv"]
    lines = users_csv.read_text(encoding="utf-8-sig").splitlines()
    assert lines, "users CSV is empty"

    # Print a tiny summary so running the test manually shows the outputs.
    print(f"Auth export wrote {len(written)} artifacts:")
    for name, path in written.items():
        print(f"  {name} -> {path}")
