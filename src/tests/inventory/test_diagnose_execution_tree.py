"""Diagnostic: find the test runs the execution walk does not reach.

The qTest UI reports more test runs than the walk collects, so some branch of
the Test Execution tree is never asked. This test prints the tree as the API
describes it -- every container with its id, and how many runs hang from it --
and then asks for the project's runs in the ways that do not depend on
traversing the tree at all, so the two counts can be compared:

    pytest src/tests/inventory/test_diagnose_execution_tree.py -s

It asserts nothing: it is here to be read. Throwaway, like the rest of the
inventory code.
"""

import pytest
import requests

from main.exporter import DataExporter
from main.exporter.data_exporter import BRANCHING_CONTAINERS, PROJECTS_ENDPOINT

BODY_PREVIEW = 300
### .venv\Scripts\python -m pytest src/tests/inventory/test_diagnose_execution_tree.py -s

def describe(client, label, method, path, **kwargs):
    """Issue one request and print what came back, without interpreting it.

    A request that fails outright is reported like any other answer: a
    diagnostic that raises tells us less than one that prints.
    """
    print(f"\n--- {label}")
    print(f"    {method} {path}  {kwargs.get('params') or kwargs.get('json') or ''}")

    try:
        response = client.request(method, path, **kwargs)
    except requests.RequestException as error:
        print(f"    request failed: {type(error).__name__}: {error}")
        return

    body = response.text or ""
    count = ""

    if response.ok:
        try:
            payload = response.json()
            items = payload if isinstance(payload, list) else payload.get("items", payload)
            if isinstance(items, list):
                count = f", {len(items)} items"
            if isinstance(payload, dict) and "total" in payload:
                count += f", total reported {payload['total']}"
        except ValueError:
            pass

    print(f"    status {response.status_code}{count}")
    print(f"    {body[:BODY_PREVIEW]}")


@pytest.mark.integration
def test_print_execution_tree(source_client, project_id):
    """Walk the tree and print every container, with the runs it holds."""
    exporter = DataExporter(source_client)
    releases = exporter.fetch_entities("test_plans", project_id)

    print(f"\n{len(releases)} releases in project {project_id}")

    total = 0
    containers = 0
    pending = [("root", 0, "root")]
    for release in releases:
        pending.append(("release", release["id"], f"release {release['id']} {release.get('name')!r}"))

    seen = set()
    while pending:
        parent_type, parent_id, label = pending.pop(0)
        if (parent_type, parent_id) in seen:
            continue
        seen.add((parent_type, parent_id))
        containers += 1

        runs = exporter.fetch_children(project_id, "test-runs", parent_type, parent_id)
        suites = exporter.fetch_children(project_id, "test-suites", parent_type, parent_id)
        cycles = (
            exporter.fetch_children(project_id, "test-cycles", parent_type, parent_id)
            if parent_type in BRANCHING_CONTAINERS
            else []
        )
        total += len(runs)

        print(
            f"  {label:<52} runs {len(runs):>3}  cycles {len(cycles):>3}  suites {len(suites):>3}"
        )

        for cycle in cycles:
            pending.append(("test-cycle", cycle["id"], f"  cycle {cycle['id']} {cycle.get('name')!r}"))
        for suite in suites:
            pending.append(("test-suite", suite["id"], f"    suite {suite['id']} {suite.get('name')!r}"))

    print(f"\n{total} runs across {containers} containers")
    print("Compare that total with the one the qTest UI shows for Test Execution.")


@pytest.mark.integration
def test_probe_all_test_runs(source_client, project_id):
    """Ask for the project's runs without traversing the tree."""
    base = f"{PROJECTS_ENDPOINT}/{project_id}"

    describe(source_client, "test-runs, no parent", "GET", f"{base}/test-runs")
    describe(
        source_client,
        "test-runs, explicit root",
        "GET",
        f"{base}/test-runs",
        params={"parentId": 0, "parentType": "root"},
    )
    describe(
        source_client,
        "test-runs, descendants expanded",
        "GET",
        f"{base}/test-runs",
        params={"parentId": 0, "parentType": "root", "expand": "descendants"},
    )
    describe(
        source_client,
        "search for test runs",
        "POST",
        f"{base}/search",
        params={"pageSize": 100},
        json={"object_type": "test-runs", "fields": ["*"]},
    )
