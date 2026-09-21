"""Diagnostic: does anything tie the test cases of HS to Jira?

The inventory found no Jira integration on the test cases, but it only looked
at the custom fields -- not at the description, the steps, or the defects a
case may be linked to. This asks every place a reference could hide:

    pytest src/tests/inventory/test_diagnose_jira_references.py -s

What it checks, per section of the output:

1. structured integration fields, across every test case
2. Jira URLs and issue keys in the description, precondition and custom fields
3. what each test case is linked to: requirements, runs, defects, anything
4. the defects the project holds, and whether they carry a Jira key
5. the test steps of a sample of cases
6. what the list endpoint leaves out compared to fetching one case

It asserts only what can be asserted cleanly -- no structured integration
field, and no test case linked to a defect -- and reports the rest, because
free text is a judgement call: "TP-5818" is a qTest convention, "MSTQ-4299"
is a Jira key, and only a person can say which is which.

Throwaway, like the rest of the inventory code.
"""

import re
from collections import Counter
from urllib.parse import urlparse

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
#: way -- TC-11, RQ-2, TR-23 -- so the prefixes it uses are reported apart
#: from the rest rather than counted as Jira references.
ISSUE_KEY = re.compile(r"\b([A-Z][A-Z0-9]{1,9})-\d+\b")
QTEST_PREFIXES = {prefix for prefix in PID_PREFIXES} | {"TS", "CY", "SU", "TP", "PL"}

#: Fields of a test case that hold free text a person could have typed a link
#: into. The integration would not use these -- a person would.
TEXT_FIELDS = ("description", "precondition", "name")

#: Test cases whose steps are read. One request each, so it is a sample.
STEP_SAMPLE = 10


def jira_urls(text):
    """Every Jira URL inside a piece of text."""
    return JIRA_LINK.findall(str(text or "")) if text else []


def issue_keys(text):
    """Every issue-key-shaped token inside a piece of text, by prefix."""
    return ISSUE_KEY.findall(str(text or "")) if text else []


def text_of(test_case):
    """All the free text of a test case, including its custom field values."""
    pieces = [test_case.get(field) for field in TEXT_FIELDS]
    for prop in test_case.get("properties") or []:
        if isinstance(prop, dict):
            pieces.append(prop.get("field_value_name") or prop.get("field_value"))
    return " ".join(str(piece) for piece in pieces if piece)


@pytest.fixture(scope="module")
def test_cases(source_client, project_id):
    """Every test case of the project, with its custom fields expanded."""
    cases = DataExporter(source_client).fetch_entities("test_cases", project_id)
    if not cases:
        pytest.skip("The project returned no test cases")
    return cases


def links_by_kind(source_client, project_id, artifact_type, ids):
    """What a set of artifacts is linked to, counted by kind.

    Returns the counts and, separately, the defects found: a defect is the
    one link that carries a Jira issue, so it is what this is looking for.
    """
    path = f"{PROJECTS_ENDPOINT}/{project_id}/linked-artifacts"
    kinds = Counter()
    defects = {}

    for start in range(0, len(ids), LINK_BATCH_SIZE):
        batch = ids[start:start + LINK_BATCH_SIZE]
        response = source_client.get(path, params={"type": artifact_type, "ids": batch})
        if not response.ok:
            pytest.skip(f"linked-artifacts refused {artifact_type}: {response.status_code}")

        for entry in response.json():
            for linked in entry.get("objects") or []:
                kind = DataExporter._linked_kind(linked) or "unknown"
                kinds[kind] += 1
                if kind.startswith("defect"):
                    defects[entry.get("id")] = linked

    return kinds, defects


@pytest.mark.integration
def test_no_structured_integration_on_test_cases(source_client, test_cases):
    """1. No test case carries a field an integration would have written."""
    exporter = DataExporter(source_client)
    flagged = {
        case["id"]: exporter.integration_of(case)
        for case in test_cases
        if exporter.integration_of(case)
    }

    print(f"\n1. Structured integration fields, across {len(test_cases)} test cases")
    print(f"   keys looked for : {list(INTEGRATION_KEYS)}, custom fields, web_url")
    print(f"   carrying one    : {len(flagged)}")
    for case_id, evidence in list(flagged.items())[:10]:
        print(f"     {case_id}: {evidence}")

    assert not flagged, f"{len(flagged)} test cases carry an integration field after all"


@pytest.mark.integration
def test_report_jira_in_the_free_text_of_test_cases(test_cases):
    """2. Jira URLs and issue keys typed into a test case by a person."""
    with_urls = {}
    prefixes = Counter()
    samples = {}

    for case in test_cases:
        text = text_of(case)
        urls = jira_urls(text)
        if urls:
            with_urls[case["id"]] = urls[0]
        for prefix in issue_keys(text):
            prefixes[prefix] += 1
            samples.setdefault(prefix, (case["id"], case.get("name")))

    print(f"\n2. Free text of {len(test_cases)} test cases")
    print(f"   with a Jira URL : {len(with_urls)}")
    for case_id, url in list(with_urls.items())[:10]:
        print(f"     {case_id}: {url}")

    print("   tokens shaped like an issue key, by prefix:")
    for prefix, count in prefixes.most_common():
        origin = "qTest's own numbering" if prefix in QTEST_PREFIXES else "NOT a qTest prefix"
        case_id, name = samples[prefix]
        print(f"     {prefix:<8} {count:>4}  ({origin})  e.g. {case_id} {str(name)[:40]!r}")

    unknown = {p: c for p, c in prefixes.items() if p not in QTEST_PREFIXES}
    if unknown or with_urls:
        print("   -> read these before concluding: a prefix that is not qTest's may be Jira's")
    else:
        print("   -> nothing that looks like a Jira reference")


@pytest.mark.integration
def test_no_test_case_is_linked_to_a_defect(source_client, project_id, test_cases):
    """3. What the test cases are linked to."""
    ids = [case["id"] for case in test_cases]
    kinds, defects = links_by_kind(source_client, project_id, "test-cases", ids)

    print(f"\n3. What {len(ids)} test cases are linked to")
    for kind, count in kinds.most_common():
        print(f"     {kind:<16} {count}")
    if not kinds:
        print("     nothing at all")
    print("   -> a defect hangs off an execution, not off a case:")
    print("      test_diagnose_jira_in_test_runs.py asks the runs and their logs")

    assert not defects, f"{len(defects)} test cases are linked to a defect: {list(defects)[:5]}"


@pytest.mark.integration
def test_report_the_defects_the_project_holds(source_client, project_id):
    """4. Defects are where a Jira key would live, if the integration ran."""
    exporter = DataExporter(source_client)
    response = source_client.get(
        f"{PROJECTS_ENDPOINT}/{project_id}/defects", params={"page": 1, "pageSize": 100}
    )

    print("\n4. Defects in the project")
    if not response.ok:
        print(f"   the endpoint answered {response.status_code}: {response.text[:200]}")
        return

    payload = response.json()
    defects = payload if isinstance(payload, list) else payload.get("items", [])
    print(f"   defects returned : {len(defects)}")

    for defect in defects[:10]:
        evidence = exporter.integration_of(defect) or "no Jira reference found"
        print(f"     {defect.get('id')} {str(defect.get('name'))[:40]!r}: {evidence}")

    if defects:
        print("   -> a defect exists, so the qTest-to-Jira direction was used at some point")


@pytest.mark.integration
def test_report_jira_in_the_steps_of_a_sample(source_client, test_cases):
    """5. Test steps, which the inventory never read."""
    sample = test_cases[:STEP_SAMPLE]
    print(f"\n5. Test steps of {len(sample)} of the {len(test_cases)} test cases")

    found = 0
    for case in sample:
        href = next(
            (link.get("href") for link in case.get("links") or [] if link.get("rel") == "test-steps"),
            None,
        )
        if not href:
            print(f"     {case['id']}: the payload names no test-steps link")
            continue

        # Only the path is followed, not the whole link: qTest advertises
        # these as plain http and may name a host other than the configured
        # one, and the client already knows where the instance is.
        link = urlparse(href)
        response = source_client.get(f"{link.path}?{link.query}" if link.query else link.path)
        if not response.ok:
            print(f"     {case['id']}: the steps answered {response.status_code}")
            continue

        payload = response.json()
        steps = payload if isinstance(payload, list) else payload.get("items", [])
        text = " ".join(
            str(step.get(field) or "")
            for step in steps
            if isinstance(step, dict)
            for field in ("description", "expected")
        )
        urls, keys = jira_urls(text), [k for k in issue_keys(text) if k not in QTEST_PREFIXES]
        if urls or keys:
            found += 1
            print(f"     {case['id']}: {len(steps)} steps, urls={urls[:2]} keys={set(keys)}")

    print(f"   cases whose steps mention Jira: {found} of {len(sample)}")
    if len(sample) < len(test_cases):
        print(f"   -> a sample only: raise STEP_SAMPLE to read all {len(test_cases)}")


@pytest.mark.integration
def test_report_what_the_listing_leaves_out(source_client, project_id, test_cases):
    """6. Whether fetching one case shows fields the listing does not."""
    case_id = test_cases[0]["id"]
    response = source_client.get(
        f"{PROJECTS_ENDPOINT}/{project_id}/test-cases/{case_id}",
        params={"expandProps": "true"},
    )

    print(f"\n6. The listing against one case fetched on its own ({case_id})")
    if not response.ok:
        print(f"   the endpoint answered {response.status_code}")
        return

    on_its_own = set(response.json())
    from_listing = set(test_cases[0])
    print(f"   fields from the listing   : {len(from_listing)}")
    print(f"   fields on its own         : {len(on_its_own)}")
    print(f"   only when fetched alone   : {sorted(on_its_own - from_listing) or 'none'}")
    print(f"   only in the listing       : {sorted(from_listing - on_its_own) or 'none'}")

    if on_its_own - from_listing:
        print("   -> the listing hides fields: section 1 should be re-read against these")
