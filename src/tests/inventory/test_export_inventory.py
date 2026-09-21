"""One-shot inventory of the source project.

Running this test is the simplest way to produce the tracking files at the
project root -- it exercises every inventory function in one go: the Test
Design tree (modules and their test cases), test plans, requirements and test
runs, reporting how many of each carry a Jira link.

    pytest src/tests/inventory/test_export_inventory.py -s

Like the other integration tests it is skipped when `config/qtest.env` has no
credentials. Temporary, same as the code it drives: both go away once the
inventory has been taken.
"""

import csv

import pytest

from main.exporter import DataExporter
from main.exporter.data_exporter import STANDALONE_ENTITIES, TEST_DESIGN_FILE
from main.writer import CsvWriter

JIRA_MARK = "[jira:"
EXPECTED_FILES = {"consolidated", "notes"}


def assert_present(rows, column, expected):
    """Every id of `expected` appears in `column` of some row."""
    found = {row[column] for row in rows if row[column]}
    missing = {str(item) for item in expected if item not in ("", None)} - found
    assert not missing, f"{column} missing from the CSV: {sorted(missing)[:10]}"


def read_entries(path):
    """Inventory lines of a file, ignoring headers and blank lines."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return [line for line in lines if line.strip() and not line.startswith("#")]


@pytest.mark.integration
def test_export_inventory_writes_every_entity(source_client, project_id):
    """Every inventory endpoint answers and lands in its own file."""
    written = DataExporter(source_client).export_inventory(project_id)

    assert set(written) == {TEST_DESIGN_FILE, *STANDALONE_ENTITIES}, "Some entity was not exported"

    print(f"\nInventory of project {project_id} in {source_client.base_url}")
    print(f"  {'file':<14}{'entries':>9}{'with Jira':>11}")

    for entity, path in written.items():
        assert path.is_file(), f"{path} was not written"

        entries = read_entries(path)
        integrated = [line for line in entries if JIRA_MARK in line]
        flag = "   <- empty, check the endpoint against the qTest UI" if not entries else ""
        print(f"  {entity:<14}{len(entries):>9}{len(integrated):>11}{flag}")

        for line in integrated[:3]:
            print(f"      sample: {line.strip()}")

    for entity, path in written.items():
        print(f"  {entity:<14} -> {path}")


@pytest.mark.integration
def test_export_consolidated_report(source_client, project_id):
    """The CSV report ties releases to their requirements, cases and runs."""
    report = DataExporter(source_client).build_traceability(project_id)
    written = CsvWriter(report).write()

    assert set(written) == EXPECTED_FILES, "Some file was not written"
    for name, path in written.items():
        assert path.is_file(), f"{path} was not written"

    with written["consolidated"].open(encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        consolidated = list(reader)
        columns = reader.fieldnames or []

    # Every row carries every column, empty where the chain does not reach.
    assert all(set(row) == set(columns) for row in consolidated), "Rows differ in shape"
    assert "Record Type" not in columns, "The table is no longer grouped by record type"
    assert not [name for name in columns if name.endswith("Count")], "A count column is back"

    # Nothing is left out: every artifact of the report has a row, whether or
    # not a test case reaches it.
    assert_present(consolidated, "Test Case ID", {case["id"] for case in report["test_cases"]})
    assert_present(consolidated, "Test Run ID", {row["run_id"] for row in report["rows"]})
    assert_present(
        consolidated, "Requirement ID", {item["id"] for item in report["requirements"]}
    )
    assert_present(consolidated, "Release ID", {item["id"] for item in report["releases"]})
    assert_present(consolidated, "Module ID", {item["id"] for item in report["modules"]})
    assert_present(
        consolidated,
        "Test Suite",
        {item["suite"] for item in report["containers"] if item["kind"] == "test-suite"},
    )
    assert_present(
        consolidated,
        "Test Cycle",
        {item["cycle"] for item in report["containers"] if item["kind"] == "test-cycle"},
    )

    # Every row says something: none is entirely empty.
    for row in consolidated:
        assert any(row[column] for column in columns), "A row carries no value at all"

    # The point of the join: a linked requirement rides on its test case's row.
    for case_id, linked in report["links"].items():
        for requirement_id in linked:
            assert any(
                row["Test Case ID"] == str(case_id)
                and row["Requirement ID"] == str(requirement_id)
                for row in consolidated
            ), f"Requirement {requirement_id} is not on the row of case {case_id}"

    releases = {row["release"] for row in report["rows"]}
    covered = {row["release"] for row in report["rows"] if row["requirement_ids"]}

    print(f"\nConsolidated report of project {project_id}")
    for name, path in written.items():
        print(f"  {name:<14} -> {path}")
    print(f"  columns                        : {len(columns)}")
    print(f"  rows in the consolidated table : {len(consolidated)}")
    print(f"  releases in the execution tree : {len(releases)}")
    print(f"  releases reaching requirements : {len(covered)}")
    print(f"  requirements in the project    : {len(report['requirements'])}")
    print(f"  test cases                     : {len(report['test_cases'])}")
    print(f"  traceability rows (test runs)  : {len(report['rows'])}")

    if not report["links_available"]:
        print("  NOTE: case-to-requirement links unavailable, requirement columns are empty")

    for row in report["rows"][:5]:
        print(
            f"    {row['release']} > {row['cycle']} > {row['suite']} > "
            f"{row['run']} > case {row['case_id']} > req [{row['requirement_ids']}]"
        )
