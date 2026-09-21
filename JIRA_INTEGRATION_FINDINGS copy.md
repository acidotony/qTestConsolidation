# Jira References in the Source Project — Findings

**Instance:** `healthspring.qtestnet.com` · **Project:** 24901 · **Date:** 18 September 2026

Everything below was measured through the qTest REST API, artifact by
artifact. No figure is an estimate.

## Verdict

**No artifact of this project is tied to Jira.** Test cases, test runs, test
logs, test cycles, test suites, releases and modules carry nothing an
integration would have written, and nothing links to an issue.

Two things need saying plainly, because either could be misread:

- **The project does contain defects** — seven of them, linked to ten test
  runs. They were raised **inside qTest**, in its own defect tracker, not in
  Jira. Section "Defects" below shows how that is known.
- **Requirements do carry Jira references**, but as free text: URLs pasted
  into a description and names echoing an issue key. Not integration
  metadata, and nothing that resynchronises.

## What was evaluated

| Element | How it was checked | Jira reference found |
|---|---|---|
| **Test cases** (266) | Integration fields — `external_id`, `external_system`, `jira_id`, `jira_key` — plus every custom field and `web_url` | **None** |
| **Test cases** — free text | Name, description, precondition and custom field values, searched for Jira URLs and issue keys | **None**: 0 URLs, 0 issue keys |
| **Test cases** — what they link to | `linked-artifacts` for all 266: 43 links to requirements, 75 to test runs | **No Jira object** |
| **Test cases** — hidden fields | One case fetched alone, compared with the listing | Only `agent_ids` is withheld, and holds no reference |
| **Test steps** | The steps of 10 of the 266 cases | **None** — one false positive, see below |
| **Test runs** (75) | Integration fields on every run | **None** |
| **Test runs** — free text | Everything each run carries as text | **None**: 0 URLs, 0 issue keys |
| **Test runs** — what they link to | `linked-artifacts` for all 75: 75 links to test cases, 10 to defects | **No Jira object** — the defects are internal, see below |
| **Test runs** — hidden fields | One run fetched alone, compared with the walk | Nothing withheld |
| **Test logs** (64, across the 75 runs) | Every log of every run, for defect records and Jira links | **None** — 3 logs name a defect, all internal |
| **Releases** (25), **cycles** (26), **suites** (58) | Integration fields, and free text on the releases | **None** |
| **Requirements** (510) | Integration fields, custom fields, descriptions | **The one exception — see below** |

## Defects

Seven defects are linked to ten test runs, and three of them are recorded on
a test log. They are qTest's own, not Jira's:

| Evidence | What it says |
|---|---|
| `is_internal: true` on every one | qTest marks the defects it raised itself |
| `connection_id: 0` | none of them reaches an external tracker |
| Identifiers `DF-3` … `DF-10` | qTest's own numbering; a Jira-backed defect would carry the issue key |
| Descriptions naming `qasymphony.com`, qTest eXplorer 5.0.2.1, and a 2015 session on `kmstechnology07.qtestnet.com` | they are the sample data qTest ships with, recorded by its vendor in 2015 |

So the qTest-to-Jira direction was **never used** in this project. What exists
is demonstration content.

### A correction to an earlier statement

An earlier draft of this note said the project held no defect at all, on the
strength of `GET /projects/24901/defects` returning an empty list. That
endpoint's answer was misleading: the defects exist and are reachable through
the executions that link to them. The figures above come from asking the 75
test runs and their logs directly, which is where a defect actually hangs.

### The false positive worth knowing

Two test logs and one test step link to
`http://www.qasymphony.com/platform/jira-integration.html`. That is qTest's
own marketing page about its Jira integration — qaSymphony is the vendor of
qTest. It matched only because the address contains the word "jira". It is
not a reference to an issue.

## The exception: requirements

Of the 510 requirements:

| | Count |
|---|---|
| Carrying a **structured** integration field | **0** |
| Carrying a Jira URL inside the **description text** | 11 |
| …of those, pointing at a real Jira host | 8 |
| …of those, pointing at documentation rather than an issue | 3 |
| **Named** like an issue key, all `MSTQ-####` | 42 |
| Both named like a key and carrying a URL | 2 |

Even here there is no integration metadata. What there is, is free text:
someone pasted a link, and some requirements were named after the issue they
came from. Neither will resynchronise in another instance.

The eight real links point at **two different Jira instances**:

| Jira host | Links |
|---|---|
| `jira.healthspring-jira-prod.aws.zilverton.com` | 6 |
| `jira.express-scripts.com` | 2 |

## What this proves, and what it does not

**It proves** that nothing in this project depends on a Jira connection to be
readable, and that migrating it does not require Jira to be migrated first.
Every artifact stands on its own.

**It does not prove that the integration is switched off.** A connection
configured but never used would leave exactly this. Whether Jira is connected
to this project, and whether it should be connected to the target, is a
question for whoever administers the instance — it is a setting, not data,
and it is not visible through the API.

## What was not examined

| Not examined | Why it matters, or does not |
|---|---|
| Test steps of the remaining 256 test cases | 10 were read as a sample. Reading all of them is one setting away |
| Attachments | A document could mention a ticket, but that is not a reference the migration has to preserve |
| The project's integration settings | Not reachable through the API, and not accessible with the permissions available |

## How these figures can be reproduced

```bash
pytest src/tests/inventory/test_diagnose_jira_references.py -s    # test cases, steps, requirements
pytest src/tests/inventory/test_diagnose_jira_in_test_runs.py -s  # runs, logs, defects, containers
pytest src/tests/inventory/test_export_inventory.py -s            # the counts per artifact
```

Each prints one section per row of the tables above. Every detector was first
shown to fire against planted data — a check that never fires proves nothing
— and only then run against the instance. The defects reported here are
precisely what that discipline turned up: an earlier check had concluded
there were none.
