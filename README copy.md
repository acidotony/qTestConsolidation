# qTestConsolidation

Project to migrate artifacts (test plans, test cases, test runs, requirements,
etc.) between two Tricentis qTest instances, via the REST API.

## Structure

```
qTestConsolidation/
├── requirements.txt
├── config/           # environment, user and credential definitions
├── src/
│   ├── main/         # production code (API clients, extraction, injection)
│   └── tests/        # validation tests (pytest)
│       ├── inventory/    # the tests that took stock of the source instance
│       ├── input/        # what the tests read: which artifacts to work on
│       └── output/       # what they produce
│           ├── inventory/    # the reference the migration is tracked
│           │                 # against, committed on purpose
│           └── screenshots/  # each instance as a person would see it,
│                             # before in HS and after in HCSC
└── migration/        # where each artifact has got to
    ├── exported/     # read out of the source, waiting to be injected
    ├── imported/     # already injected into the target: it crossed
    └── id_map.json   # what each source artifact became in the target
```

## Setup

```bash
python -m venv .venv
source .venv/bin/activate        # on Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Configuration

Connection settings live in `config/` as `.env` files (flat `KEY=VALUE` pairs).
Copy the template and fill in the real values:

```bash
cp config/qtest.example.env config/qtest.env
```

`config/qtest.env` holds credentials and is git-ignored; only the
`*.example.env` templates are committed. Both instances share a single file,
distinguished by the `SOURCE_` and `TARGET_` prefixes: `SOURCE_*` is the HS
instance (migration origin) and `TARGET_*` is the HCSC instance (destination).

## Logging

Every module logs through `logging.getLogger(__name__)` and configures nothing
by itself. Entry-point scripts turn logging on once:

```python
import logging
from main.logging_config import configure_logging

configure_logging(logging.INFO)                      # console
configure_logging(logging.DEBUG, "migration.log")    # console + file
```

## Authentication & Authorization export

The project now includes an `AuthExporter` to collect authentication and
authorization data (users, roles, permissions) from a qTest instance. Use it
from Python like this:

```python
from main.client import RestClient
from main.exporter import AuthExporter

client = RestClient.for_source()
exporter = AuthExporter(client)
artifacts = exporter.export_all()
print(artifacts)
```

Outputs are written under `migration/imported/` as JSON and CSV files.


`INFO` reports the migration steps (requests issued, responses received, files
written). `DEBUG` adds the internal traces. Credentials are never logged.

## Running the tests

```bash
pytest src/tests
```
