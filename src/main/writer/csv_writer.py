"""Consolidated traceability report as a single CSV file.

`CsvWriter` takes the report built by `DataExporter.build_traceability` and
writes one flat table whose grain is the test case: every row is a test case,
with the module holding it, the requirement it covers and the run that
executed it side by side.

A test case with three runs and two requirements yields six rows, its own
columns repeated on each. That duplication is the point: every row can be
followed, and marked off, on its own while the migration runs.

A test case never executed keeps its row, with the execution columns empty.
An entity no test case reaches -- a requirement nothing covers, an empty
folder, a release with nothing executed -- gets a row of its own with the test
case columns empty: it has to be migrated all the same, so the file has to
name it. Which columns a row fills is what says what it is.

Written under `src/tests/output/inventory`, alongside the inventory text files.

It computes nothing about the instance: every relation comes from the report.
Temporary, like the inventory it presents.
"""

from __future__ import annotations

import csv
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Where the report is written, alongside the inventory text files. Committed
#: on purpose: the migration is tracked against it.
INVENTORY_DIR = Path(__file__).resolve().parents[3] / "src" / "tests" / "output" / "inventory"

DEFAULT_REPORT_FILE = "qtest_consolidated"
CSV_EXTENSION = ".csv"

#: Written with a BOM so Excel opens the file as UTF-8 and keeps accents.
CSV_ENCODING = "utf-8-sig"

#: Every column, in reading order: the test case first, since it is the grain,
#: then where it lives, what it covers, how it was executed, and the columns
#: left empty for tracking the migration.
COLUMNS = (
    "Test Case ID",
    "Test Case",
    "Module ID",
    "Module",
    # No Test Case Jira column: no test case of this project references Jira,
    # and the integration is only ever recorded on the requirements.
    "Requirement ID",
    "Requirement",
    "Requirement Jira",
    "Release ID",
    "Release",
    "Test Cycle",
    "Test Suite",
    "Test Run ID",
    "Test Run",
    # Left empty on purpose: filled in by hand while the migration runs.
    "Migration Status",
    "Target ID",
    "Migration Notes",
)


class CsvWriter:
    """Writes the consolidated traceability report as one CSV file."""

    def __init__(
        self,
        report: dict[str, Any],
        output_dir: Path | str = INVENTORY_DIR,
        delimiter: str = ",",
    ) -> None:
        self.report = report
        self._output_dir = Path(output_dir)
        self._delimiter = delimiter
        # Filled once per build: recomputing it per row would be quadratic.
        self._requirement_index: dict[Any, dict[str, Any]] = {}
        logger.debug(
            "CSV writer ready, output folder: %s, delimiter %r", self._output_dir, delimiter
        )

    @property
    def report(self) -> dict[str, Any]:
        """The traceability report to lay out."""
        return self._report

    @report.setter
    def report(self, value: dict[str, Any]) -> None:
        if not value:
            logger.error("Cannot set the report: it is missing")
            raise ValueError("report is required")
        self._report = value

    @property
    def output_dir(self) -> Path:
        """Folder the files are written to."""
        return self._output_dir

    @property
    def delimiter(self) -> str:
        """Field separator. Use ';' where Excel expects it by locale."""
        return self._delimiter

    # -- report --------------------------------------------------------------

    def write(self, file_name: str = DEFAULT_REPORT_FILE) -> dict[str, Path]:
        """Write the consolidated table and its notes, and return both files."""
        target = self._resolve_path(file_name)
        rows = self.build_rows()
        logger.info("Writing %s rows to %s", len(rows), target)

        self._output_dir.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding=CSV_ENCODING, newline="") as csv_file:
            writer = csv.DictWriter(
                csv_file,
                fieldnames=COLUMNS,
                delimiter=self._delimiter,
                quoting=csv.QUOTE_MINIMAL,
                restval="",
            )
            writer.writeheader()
            writer.writerows(rows)

        logger.info("Wrote %s (%s bytes)", target.name, target.stat().st_size)
        return {"consolidated": target, "notes": self.write_notes(file_name)}

    def build_rows(self) -> list[dict[str, Any]]:
        """One row per test case, run and requirement it is tied to."""
        self._requirement_index = {
            requirement.get("id"): requirement for requirement in self._report["requirements"]
        }

        runs_by_case: dict[Any, list[dict[str, Any]]] = {}
        for run in self._report["rows"]:
            runs_by_case.setdefault(run["case_id"], []).append(run)

        rows = []
        for case in self._report["test_cases"]:
            for run in runs_by_case.get(case.get("id")) or [None]:
                for requirement in self._requirements_of(case.get("id")):
                    rows.append(self._row(case, run, requirement))

        rows.extend(self._runs_without_a_case(runs_by_case))
        # Everything no test case reaches still has to be migrated, so it is
        # listed too, with the test case columns empty.
        rows.extend(self._requirements_without_a_case())
        rows.extend(self._modules_without_a_case())
        rows.extend(self._containers_without_a_run())
        rows.extend(self._releases_without_a_run())

        # Read with get: a row only carries the run and requirement columns
        # when it reaches them.
        executed = sum(1 for row in rows if row.get("Test Run ID"))
        covered = sum(1 for row in rows if row.get("Requirement ID"))
        logger.info(
            "Built %s rows over %s test cases: %s carry a run, %s carry a requirement",
            len(rows),
            len(self._report["test_cases"]),
            executed,
            covered,
        )
        return rows

    # -- rows ----------------------------------------------------------------

    def _row(
        self,
        case: dict[str, Any],
        run: dict[str, Any] | None,
        requirement: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """One row: a test case, with what it covers and how it was executed."""
        row = {
            "Test Case ID": case.get("id", ""),
            "Test Case": case.get("name") or "",
            "Module ID": case.get("module_id", ""),
            "Module": case.get("module") or self._missing_module(case),
        }

        if requirement is not None:
            row.update({
                "Requirement ID": requirement.get("id", ""),
                "Requirement": requirement.get("name") or "",
                "Requirement Jira": requirement.get("jira") or "",
            })

        if run is not None:
            row.update({
                "Release ID": run["release_id"],
                "Release": run["release"],
                "Test Cycle": run["cycle"],
                "Test Suite": run["suite"],
                "Test Run ID": run["run_id"],
                "Test Run": run["run"],
            })

        return row

    def _runs_without_a_case(
        self,
        runs_by_case: dict[Any, list[dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        """Runs whose test case is not in the project's list.

        They have no test case row to ride on, and dropping them would hide an
        execution that exists, so they are written with the case columns empty.
        """
        known = {case.get("id") for case in self._report["test_cases"]}
        orphans = [
            run
            for case_id, runs in runs_by_case.items()
            if case_id not in known
            for run in runs
        ]

        if orphans:
            logger.warning(
                "%s test runs execute a test case that is not in the project's list", len(orphans)
            )
        return [self._row({"id": run["case_id"], "name": run["case"]}, run, None) for run in orphans]

    def _requirements_without_a_case(self) -> list[dict[str, Any]]:
        """Requirements no test case covers, which have to be migrated too."""
        linked = {
            str(requirement_id)
            for requirement_ids in self._report["links"].values()
            for requirement_id in requirement_ids
        }
        alone = [
            requirement
            for requirement in self._report["requirements"]
            if str(requirement.get("id")) not in linked
        ]

        logger.info(
            "%s of %s requirements are covered by no test case", len(alone), len(self._report["requirements"])
        )
        return [
            {
                "Requirement ID": requirement.get("id", ""),
                "Requirement": requirement.get("name") or "",
                "Requirement Jira": requirement.get("jira") or "",
            }
            for requirement in alone
        ]

    def _modules_without_a_case(self) -> list[dict[str, Any]]:
        """Test Design folders holding no test case."""
        holding = {case.get("module_id") for case in self._report["test_cases"]}
        alone = [
            module for module in self._report["modules"] if module.get("id") not in holding
        ]

        logger.info(
            "%s of %s modules hold no test case", len(alone), len(self._report["modules"])
        )
        return [
            {"Module ID": module.get("id", ""), "Module": module.get("name") or ""}
            for module in alone
        ]

    def _containers_without_a_run(self) -> list[dict[str, Any]]:
        """Test cycles and suites holding no run, which are migrated all the same."""
        containers = self._report.get("containers") or []
        alone = [container for container in containers if not container.get("holds_runs")]

        logger.info(
            "%s of %s cycles and suites hold no test run", len(alone), len(containers)
        )
        return [
            {
                "Release ID": container["release_id"],
                "Release": container["release"],
                "Test Cycle": container["cycle"],
                "Test Suite": container["suite"],
            }
            for container in alone
        ]

    def _releases_without_a_run(self) -> list[dict[str, Any]]:
        """Releases nothing at all hangs from.

        A release named by a run or by a cycle already appears on that row, so
        only the ones nothing points at need one of their own.
        """
        used = {str(run["release_id"]) for run in self._report["rows"] if run["release_id"]}
        used.update(
            str(container["release_id"])
            for container in self._report.get("containers") or []
            if container["release_id"]
        )
        alone = [
            release
            for release in self._report["releases"]
            if str(release.get("id")) not in used
        ]

        logger.info(
            "%s of %s releases have nothing executed under them",
            len(alone),
            len(self._report["releases"]),
        )
        return [
            {"Release ID": release.get("id", ""), "Release": release.get("name") or ""}
            for release in alone
        ]

    def _requirements_of(self, case_id: Any) -> list[dict[str, Any] | None]:
        """Requirements linked to a test case, or `[None]` when there are none.

        The single `None` is what keeps a case without requirements on a row of
        its own instead of dropping it.
        """
        linked = [
            self._requirement_index.get(requirement_id, {"id": requirement_id})
            for requirement_id in self._report["links"].get(case_id, [])
        ]
        return linked or [None]

    def _missing_module(self, case: dict[str, Any]) -> str:
        """Label for a case whose module is not in the tree."""
        module_id = case.get("module_id")
        if module_id is None:
            return ""
        if module_id in {module.get("id") for module in self._report["modules"]}:
            return ""
        return f"(module {module_id} not in the tree)"

    # -- notes ---------------------------------------------------------------

    def write_notes(self, file_name: str = DEFAULT_REPORT_FILE) -> Path:
        """What the reader needs to know to trust the rows.

        Kept out of the CSV on purpose: a comment header would break the tools
        the format was chosen for.
        """
        target = self._output_dir / f"{file_name}_notes.txt"
        logger.info("Writing the report notes to %s", target)

        target.write_text("\n".join(self._notes()) + "\n", encoding="utf-8")
        return target

    def _notes(self) -> list[str]:
        links_note = (
            "Requirement columns come from the test case to requirement links."
            if self._report["links_available"]
            else "WARNING: the case-to-requirement links could not be read, so the "
            "requirement columns are empty. Empty does not mean no coverage."
        )
        return [
            f"qTest consolidated traceability - project {self._report['project_id']}",
            f"Source: {self._report['base_url']}, "
            f"generated {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
            "One row per test case, with the module holding it, the requirement it",
            "covers and the run that executed it on the same row.",
            "",
            "A test case with three runs and two requirements yields six rows, its own",
            "columns repeated on each: every row can be followed, and marked off, on",
            "its own. A test case never executed keeps its row with the execution",
            "columns empty, and one covering no requirement keeps its row with the",
            "requirement columns empty.",
            "",
            "Every artifact of the project has a row, whether or not a test case",
            "reaches it, because the migration has to carry all of them across:",
            "  a requirement no test case covers    -> only the requirement columns",
            "  a Test Design folder holding no case -> only the module columns",
            "  a test cycle or suite holding no run -> the release, cycle and suite",
            "  a release nothing hangs from         -> only the release columns",
            "Which columns a row fills is what says what it is; there is no record",
            "type column to read.",
            "",
            "Requirement Jira carries the evidence that a requirement came from Jira.",
            "In this project it only ever appears on those rows: the requirements",
            "imported from Jira are precisely the ones no test case covers.",
            "",
            "qTest has no release-to-requirement relation: the release, cycle and",
            "suite of a row are those of the run, and the requirement is the one",
            "linked to the test case. A requirement is tied to a release only through",
            "a run that executes a case covering it.",
            links_note,
            "",
            "Migration Status, Target ID and Migration Notes are left empty for",
            "tracking the migration by hand.",
        ]

    # -- helpers -------------------------------------------------------------

    def _resolve_path(self, file_name: str) -> Path:
        name = (file_name or "").strip()
        if not name:
            logger.error("Cannot resolve the target path: file_name is missing")
            raise ValueError("file_name is required")
        if not name.lower().endswith(CSV_EXTENSION):
            name = f"{name}{CSV_EXTENSION}"
        return self._output_dir / name
