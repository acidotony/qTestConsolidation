"""Shared fixtures for the tests that talk to a live qTest instance.

Everything here resolves from `config/qtest.env`. When the settings are
missing -- any machine without access to the customer instances -- the tests
that depend on these fixtures are skipped, never failed. The source and the
target are asked for separately, so reading HS keeps working while the
proof-of-concept project in HCSC does not exist yet.
"""

import logging
import os

import pytest
from dotenv import dotenv_values

from main.client import DEFAULT_ENV_FILE, MissingConfigurationError, RestClient
from main.logging_config import configure_logging

SOURCE_PROJECT_ID_ENV_VAR = "SOURCE_QTEST_PROJECT_ID"
TARGET_PROJECT_ID_ENV_VAR = "TARGET_QTEST_PROJECT_ID"


def configured_project_id(variable):
    """The project id held by a setting, or a skip when it is not set."""
    values = {**dotenv_values(DEFAULT_ENV_FILE), **os.environ}
    configured = (values.get(variable) or "").strip()
    if not configured:
        pytest.skip(f"{variable} is not set in {DEFAULT_ENV_FILE}")
    return int(configured)


@pytest.fixture(scope="session")
def source_client():
    """A client for the HS instance, or a skip when it is not configured."""
    configure_logging(logging.INFO)
    try:
        return RestClient.for_source()
    except MissingConfigurationError as error:
        pytest.skip(f"Source instance not configured: {error}")


@pytest.fixture(scope="session")
def project_id():
    """The HS project to read, taken from the `.env` settings."""
    return configured_project_id(SOURCE_PROJECT_ID_ENV_VAR)


@pytest.fixture(scope="session")
def target_client():
    """A client for the HCSC instance, or a skip when it is not configured."""
    configure_logging(logging.INFO)
    try:
        return RestClient.for_target()
    except MissingConfigurationError as error:
        pytest.skip(f"Target instance not configured: {error}")


@pytest.fixture(scope="session")
def target_project_id():
    """The HCSC project to write into: the one made for the proof of concept."""
    return configured_project_id(TARGET_PROJECT_ID_ENV_VAR)
