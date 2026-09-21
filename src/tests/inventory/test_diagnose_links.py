"""Diagnostic: find how this instance exposes requirement to test case links.

The qTest UI shows linked test cases on a requirement, but the endpoint the
exporter asks returns none. This test puts the same question several ways and
prints the raw answer of each, so the call that works can be read off the
output:

    pytest src/tests/inventory/test_diagnose_links.py -s

It asserts nothing about the links -- it is here to be read, not to pass or
fail. Throwaway, like the rest of the inventory code: delete it once the right
call is known.

The requirement it probes is the first one the project returns, unless
SOURCE_QTEST_REQUIREMENT_ID is set in config/qtest.env.
"""

import json
import os

import pytest
import requests
from dotenv import dotenv_values

from main.client import DEFAULT_ENV_FILE
from main.exporter import DataExporter

REQUIREMENT_ID_ENV_VAR = "SOURCE_QTEST_REQUIREMENT_ID"
BODY_PREVIEW = 600


def probe(client, label, path, params=None):
    """Issue one request and print what came back, without interpreting it.

    A request that fails outright is reported like any other answer: a
    diagnostic that raises tells us less than one that prints.
    """
    print(f"\n--- {label}")
    print(f"    GET {path}  params={params}")

    try:
        response = client.get(path, params=params)
    except requests.RequestException as error:
        print(f"    request failed: {type(error).__name__}: {error}")
        return

    body = response.text or ""
    print(f"    status {response.status_code}, {len(body)} bytes")
    print(f"    {body[:BODY_PREVIEW]}")
    if len(body) > BODY_PREVIEW:
        print(f"    ... ({len(body) - BODY_PREVIEW} more bytes)")


@pytest.mark.integration
def test_diagnose_requirement_links(source_client, project_id):
    """Ask for the links of one requirement in every shape worth trying."""
    exporter = DataExporter(source_client)

    values = {**dotenv_values(DEFAULT_ENV_FILE), **os.environ}
    configured = (values.get(REQUIREMENT_ID_ENV_VAR) or "").strip()

    if configured:
        requirement_id = int(configured)
    else:
        requirements = exporter.fetch_entities("requirements", project_id)
        if not requirements:
            pytest.skip("The project returned no requirements to probe")
        requirement_id = requirements[0]["id"]
        print(f"\nProbing requirement {requirement_id}: {requirements[0].get('name')!r}")

    base = f"/api/v3/projects/{project_id}"

    # The comma-joined ids the exporter sends today.
    probe(
        source_client,
        "linked-artifacts, type=requirements, ids comma-joined",
        f"{base}/linked-artifacts",
        {"type": "requirements", "ids": str(requirement_id)},
    )
    # The same call with the parameter repeated: ids=<id>, which is how many
    # APIs expect a list. This is the likeliest reason the answer came empty.
    probe(
        source_client,
        "linked-artifacts, type=requirements, ids repeated",
        f"{base}/linked-artifacts",
        {"type": "requirements", "ids": [requirement_id]},
    )
    probe(
        source_client,
        "linked-artifacts, type singular",
        f"{base}/linked-artifacts",
        {"type": "requirement", "ids": [requirement_id]},
    )
    probe(
        source_client,
        "links under the requirement itself",
        f"{base}/requirements/{requirement_id}/linked-artifacts",
    )
    probe(
        source_client,
        "the requirement payload, expanded",
        f"{base}/requirements/{requirement_id}",
        {"expandProps": "true"},
    )


@pytest.mark.integration
def test_diagnose_test_case_links(source_client, project_id):
    """Ask the same from the test case side, for one case of the project."""
    exporter = DataExporter(source_client)
    test_cases = exporter.fetch_entities("test_cases", project_id)

    if not test_cases:
        pytest.skip("The project returned no test cases to probe")

    case_id = test_cases[0]["id"]
    print(f"\nProbing test case {case_id}: {test_cases[0].get('name')!r}")
    base = f"/api/v3/projects/{project_id}"

    probe(
        source_client,
        "linked-artifacts, type=test-cases, ids repeated",
        f"{base}/linked-artifacts",
        {"type": "test-cases", "ids": [case_id]},
    )
    probe(
        source_client,
        "links under the test case itself",
        f"{base}/test-cases/{case_id}/linked-artifacts",
    )

    response = source_client.get(f"{base}/test-cases/{case_id}", params={"expandProps": "true"})
    if response.ok:
        payload = response.json()
        print("\n--- keys of the test case payload")
        print(f"    {sorted(payload)}")
        print(json.dumps(payload, indent=2)[:BODY_PREVIEW])
