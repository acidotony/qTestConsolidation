"""Diagnostic: does anything tie the executions of HS to Jira?

A defect is reported from a test run and linked to that execution, not to the
test case, so the execution side needs asking on its own terms:

    pytest src/tests/inventory/test_diagnose_jira_in_test_runs.py -s

What it checks, per section of the output:

1. structured integration fields on the test runs themselves
2. Jira URLs and issue keys in what a run carries as text
3. what each run is linked to — defects above all
4. the test logs of each run, where an execution records a defect
5. the cycles, suites and releases the runs hang from
6. whether fetching one run shows fields the walk does not

It asserts what can be asserted cleanly -- no structured integration field,
and no execution linked to a defect -- and reports the rest.

Throwaway, like the rest of the inventory code.
"""

import re
from collections import Counter

import pytest

from main.exporter import DataExporter
from main.exporter.data_exporter import (
    INTEGRATION_KEYS,
    JIRA_LINK,
    LINK_BATCH_SIZE,
    PID_PREFIXES,
    PROJECTS_ENDPOINT,
)

#: A token shaped like an issue key. qTest numbers its own artifacts the same
#: way, so its prefixes are reported apart rather than counted as Jira.
ISSUE_KEY = re.compile(r"\b([A-Z][A-Z0-9]{1,9})-\d+\b")
QTEST_PREFIXES = set(PID_PREFIXES) | {"TS", "CY", "SU", "TP", "PL"}

#: Fields of an execution that hold text a person could have typed into.
TEXT_FIELDS = ("name", "description", "note", "summary")

#: Runs whose logs are read. One request each; lower it to shorten the run.
LOG_SAMPLE = 75


def jira_urls(text):
    return JIRA_LINK.findall(str(text or "")) if text else []


def issue_keys(text):
    return ISSUE_KEY.findall(str(text or "")) if text else []


def is_internal(defect):
    """Whether a defect belongs to qTest's own tracker rather than Jira.

    qTest marks its own with `is_internal`, and one raised in an external
    tracker names the connection that reaches it. Either says enough.
    """
    if defect.get("is_internal") is not None:
        return bool(defect["is_internal"])
    return not defect.get("connection_id")


def text_of(entity):
    """All the free text of an entity, its custom field values included."""
    pieces = [entity.get(field) for field in TEXT_FIELDS]
    for prop in entity.get("properties") or []:
        if isinstance(prop, dict):
            pieces.append(prop.get("field_value_name") or prop.get("field_value"))
    return " ".join(str(piece) for piece in pieces if piece)


def report_free_text(entities, label):
    """Print the Jira URLs and issue keys found across a set of entities."""
    with_urls = {}
    prefixes = Counter()

    for entity in entities:
        text = text_of(entity)
        urls = jira_urls(text)
        if urls:
            with_urls[entity.get("id")] = urls[0]
        for prefix in issue_keys(text):
            prefixes[prefix] += 1

    print(f"   {label}: {len(entities)} entities")
    print(f"     with a Jira URL : {len(with_urls)}")
    for entity_id, url in list(with_urls.items())[:10]:
        print(f"       {entity_id}: {url}")
    for prefix, count in prefixes.most_common():
        origin = "qTest's own numbering" if prefix in QTEST_PREFIXES else "NOT a qTest prefix"
        print(f"     {prefix:<8} {count:>4}  ({origin})")
    if not with_urls and not prefixes:
        print("     nothing that looks like a Jira reference")
    return with_urls


@pytest.fixture(scope="module")
def test_runs(source_client, project_id):
    """Every test run of the project, wherever it hangs in the execution tree."""
    runs = DataExporter(source_client).fetch_entities("test_runs", project_id)
    if not runs:
        pytest.skip("The project returned no test runs")
    return runs


@pytest.fixture(scope="module")
def containers(source_client, project_id):
    """The cycles and suites the runs hang from, plus the releases."""
    exporter = DataExporter(source_client)
    releases = exporter.fetch_entities("test_plans", project_id)
    return exporter.enumerate_containers(project_id, releases), releases


@pytest.mark.integration
def test_no_structured_integration_on_test_runs(source_client, test_runs):
    """1. No test run carries a field an integration would have written."""
    exporter = DataExporter(source_client)
    flagged = {
        run["id"]: exporter.integration_of(run)
        for run in test_runs
        if exporter.integration_of(run)
    }

    print(f"\n1. Structured integration fields, across {len(test_runs)} test runs")
    print(f"   keys looked for : {list(INTEGRATION_KEYS)}, custom fields, web_url")
    print(f"   carrying one    : {len(flagged)}")
    for run_id, evidence in list(flagged.items())[:10]:
        print(f"     {run_id}: {evidence}")

    assert not flagged, f"{len(flagged)} test runs carry an integration field after all"


@pytest.mark.integration
def test_report_jira_in_the_free_text_of_test_runs(test_runs):
    """2. Jira URLs and issue keys in what a run carries as text."""
    print(f"\n2. Free text of the test runs")
    report_free_text(test_runs, "test runs")


@pytest.mark.integration
def test_no_test_run_is_linked_to_a_jira_defect(source_client, project_id, test_runs):
    """3. What the executions are linked to — where a defect hangs.

    A defect linked here is not necessarily a Jira issue. qTest has a defect
    tracker of its own, and a defect it raised itself carries `is_internal`
    and no connection to an external system. Only an external one means the
    qTest-to-Jira direction was used, so that is what this asserts; internal
    ones are counted and reported.
    """
    path = f"{PROJECTS_ENDPOINT}/{project_id}/linked-artifacts"
    ids = [run["id"] for run in test_runs]
    kinds = Counter()
    defects = {}

    for start in range(0, len(ids), LINK_BATCH_SIZE):
        batch = ids[start:start + LINK_BATCH_SIZE]
        response = source_client.get(path, params={"type": "test-runs", "ids": batch})
        if not response.ok:
            pytest.skip(f"linked-artifacts refused test-runs: {response.status_code}")

        for entry in response.json():
            for linked in entry.get("objects") or []:
                kind = DataExporter._linked_kind(linked) or "unknown"
                kinds[kind] += 1
                if kind.startswith("defect"):
                    defects[entry.get("id")] = linked

    internal = {run: defect for run, defect in defects.items() if is_internal(defect)}
    external = {run: defect for run, defect in defects.items() if not is_internal(defect)}

    print(f"\n3. What {len(ids)} test runs are linked to")
    for kind, count in kinds.most_common():
        print(f"     {kind:<16} {count}")
    if not kinds:
        print("     nothing at all")

    print(f"   defects raised inside qTest : {len(internal)}")
    for run_id, defect in list(internal.items())[:10]:
        print(f"     run {run_id} -> {defect.get('pid')} (id {defect.get('id')})")
    print(f"   defects in an external tracker : {len(external)}")
    for run_id, defect in list(external.items())[:10]:
        print(f"     run {run_id} -> {defect.get('pid')} (id {defect.get('id')}) {defect}")

    assert not external, (
        f"{len(external)} test runs are linked to a defect outside qTest: {list(external)[:5]}"
    )


@pytest.mark.integration
def test_report_the_logs_of_each_run(source_client, project_id, test_runs):
    """4. Test logs: the execution record, and where a defect is noted."""
    sample = test_runs[:LOG_SAMPLE]
    print(f"\n4. Test logs of {len(sample)} of the {len(test_runs)} runs")

    total_logs = 0
    with_defects = {}
    with_jira = {}
    refused = 0

    for run in sample:
        response = source_client.get(
            f"{PROJECTS_ENDPOINT}/{project_id}/test-runs/{run['id']}/test-logs"
        )
        if not response.ok:
            refused += 1
            continue

        payload = response.json()
        logs = payload if isinstance(payload, list) else payload.get("items", [])
        total_logs += len(logs)

        for log in logs:
            if not isinstance(log, dict):
                continue
            if log.get("defects"):
                with_defects[run["id"]] = log["defects"]
            urls = jira_urls(" ".join(str(value) for value in log.values()))
            if urls:
                with_jira[run["id"]] = urls[0]

    external = {
        run_id: [defect for defect in defects if not is_internal(defect)]
        for run_id, defects in with_defects.items()
    }
    external = {run_id: defects for run_id, defects in external.items() if defects}

    print(f"   logs read            : {total_logs}")
    print(f"   runs whose logs were refused : {refused}")
    print(f"   logs naming a defect : {len(with_defects)}, of which outside qTest: {len(external)}")
    for run_id, defects in list(with_defects.items())[:10]:
        summary = ", ".join(
            f"{defect.get('pid')} {str(defect.get('summary'))[:40]!r}" for defect in defects
        )
        print(f"     run {run_id}: {summary}")
    print(f"   logs with a Jira URL : {len(with_jira)}")
    for run_id, url in list(with_jira.items())[:10]:
        print(f"     run {run_id}: {url}")

    if not with_defects and not with_jira:
        print("   -> no execution recorded a defect or a Jira link")


@pytest.mark.integration
def test_report_the_containers_the_runs_hang_from(source_client, containers):
    """5. Cycles, suites and releases: the rest of the execution tree."""
    tree, releases = containers
    exporter = DataExporter(source_client)

    cycles = [item for item in tree if item["kind"] == "test-cycle"]
    suites = [item for item in tree if item["kind"] == "test-suite"]

    print(f"\n5. The execution tree: {len(releases)} releases, "
          f"{len(cycles)} cycles, {len(suites)} suites")

    flagged = {
        item.get("id"): exporter.integration_of(item)
        for item in [*tree, *releases]
        if exporter.integration_of(item)
    }
    print(f"   carrying an integration field : {len(flagged)}")
    for item_id, evidence in list(flagged.items())[:10]:
        print(f"     {item_id}: {evidence}")

    report_free_text(releases, "releases")


@pytest.mark.integration
def test_report_what_the_walk_leaves_out(source_client, project_id, test_runs):
    """6. Whether fetching one run shows fields the walk does not."""
    run_id = test_runs[0]["id"]
    response = source_client.get(f"{PROJECTS_ENDPOINT}/{project_id}/test-runs/{run_id}")

    print(f"\n6. The walk against one run fetched on its own ({run_id})")
    if not response.ok:
        print(f"   the endpoint answered {response.status_code}")
        return

    on_its_own = set(response.json())
    from_walk = set(test_runs[0])
    print(f"   fields from the walk    : {len(from_walk)}")
    print(f"   fields on its own       : {len(on_its_own)}")
    print(f"   only when fetched alone : {sorted(on_its_own - from_walk) or 'none'}")

    if on_its_own - from_walk:
        print("   -> the walk hides fields: section 1 should be re-read against these")
