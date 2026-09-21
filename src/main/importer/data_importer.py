"""Injection of test artifacts into the target qTest instance (HCSC).

`DataImporter` is a consumer of `RestClient`: it owns the decisions about which
endpoints to call and how to build the payloads, while the client only carries
the request.

`import_standalone` is the mirror of the exporter's `export_standalone`, and
orchestrates the steps of getting one artifact across: read the file the
export wrote, delete whatever an earlier run left in the target, strip what
the target will not accept, create it, record the id HCSC assigned next to the
id it had in HS, move the file to `migration/imported/`, and capture the
target's page.

Two of those exist so the migration can be run again and end in the same
place: the deletion, which is what keeps a second run from duplicating, and
the move, since which folder holds a file is the record of where its artifact
got to.

The id map -- `migration/id_map.json` -- is what later lets the relations be
rebuilt, and what fills the Target ID column of the consolidated report.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from main.artifacts import (
    UnknownArtifactError,
    resolve_artifact_type,
    standalone_file_name,
)
from main.client import RestClient, WebClient
from main.writer import EXPORTED_DIR, IMPORTED_DIR

logger = logging.getLogger(__name__)

PROJECTS_ENDPOINT = "/api/v3/projects"

#: Endpoint that creates each kind of artifact in the target instance.
CREATE_PATHS = {
    "requirement": PROJECTS_ENDPOINT + "/{project_id}/requirements",
    "test-case": PROJECTS_ENDPOINT + "/{project_id}/test-cases",
    "test-run": PROJECTS_ENDPOINT + "/{project_id}/test-runs",
    "test-cycle": PROJECTS_ENDPOINT + "/{project_id}/test-cycles",
    "test-suite": PROJECTS_ENDPOINT + "/{project_id}/test-suites",
    "release": PROJECTS_ENDPOINT + "/{project_id}/releases",
    "module": PROJECTS_ENDPOINT + "/{project_id}/modules",
}

#: What is carried across, per kind. Everything else the source sends is
#: dropped rather than forwarded: the target assigns its own id, keeps its own
#: timestamps, and numbers its own pid.
CREATABLE_FIELDS = {
    "requirement": ("name", "description"),
    "test-case": ("name", "description", "precondition"),
    "test-run": ("name",),
    "test-cycle": ("name", "description", "start_date", "end_date"),
    "test-suite": ("name", "description"),
    "release": ("name", "description", "start_date", "end_date"),
    "module": ("name", "description"),
}

#: Fields of the source that must never reach the target, called out so the
#: log can say why they were dropped rather than listing them as noise.
#:   id, pid            the target numbers its own
#:   parent_id          points at a module of the SOURCE tree
#:   properties         custom fields are defined per project, so a field id
#:                      from HS means nothing in HCSC
NEVER_FORWARDED = ("id", "pid", "parent_id", "parentId", "properties", "links", "web_url")

#: Where the id the target assigned is recorded against the id it had in HS.
ID_MAP_FILE = Path(__file__).resolve().parents[3] / "migration" / "id_map.json"

SUCCESS_STATUS_CODES = (200, 201)
DELETED_STATUS_CODES = (200, 202, 204)
NOT_FOUND = 404

#: Artifacts asked for per page while looking for one already in the target.
PAGE_SIZE = 100

#: Hard stop for that search, so a misbehaving endpoint cannot spin.
MAX_PAGES = 100

#: Keys under which a listing may nest its page of results.
ITEM_KEYS = ("items", "data", "results")


class ArtifactImportError(RuntimeError):
    """Raised when an artifact cannot be created in the target instance."""


class DataImporter:
    """Entry point for injecting artifacts into the target instance."""

    def __init__(
        self,
        client: RestClient,
        exported_dir: Path | str = EXPORTED_DIR,
        imported_dir: Path | str = IMPORTED_DIR,
        id_map_file: Path | str = ID_MAP_FILE,
        web_client: WebClient | None = None,
    ) -> None:
        self._client = client
        self._exported_dir = Path(exported_dir)
        self._imported_dir = Path(imported_dir)
        self._id_map_file = Path(id_map_file)
        self._web_client = web_client
        logger.debug(
            "Importer ready against %s, reading from %s", client.base_url, self._exported_dir
        )

    @property
    def client(self) -> RestClient:
        """The REST client used to reach the target instance."""
        return self._client

    @property
    def web_client(self) -> WebClient | None:
        """The browser client that captures the target pages, if there is one."""
        return self._web_client

    @property
    def exported_dir(self) -> Path:
        """Folder holding what the export read out of the source instance."""
        return self._exported_dir

    @property
    def imported_dir(self) -> Path:
        """Folder holding what has been injected into the target."""
        return self._imported_dir

    @property
    def id_map_file(self) -> Path:
        """File recording the id the target assigned to each source artifact."""
        return self._id_map_file

    # -- one artifact on its own ---------------------------------------------

    def import_standalone(
        self,
        artifact_type: str,
        artifact_id: Any,
        project_id: int,
    ) -> dict[str, Any]:
        """Inject one exported artifact into the target and record its new id.

        Nothing it pointed at in the source is followed or recreated: the
        artifact lands at the root of its tree in the target, and where it
        belongs is settled later, once the artifacts it refers to have been
        migrated and the id map can say what they became.

        The steps are orchestrated here, so the migration can be run again
        over the same artifact and end in the same place: whatever an earlier
        run left in the target is deleted first, the artifact is created, the
        id it was given is recorded, its file moves from `exported` to
        `imported` -- which is what says it crossed -- and the target's page
        is captured.

        Returns the artifact as the target created it.
        """
        kind = self.resolve_artifact_type(artifact_type)
        logger.info("Importing %s %s into %s", kind, artifact_id, self._client.base_url)

        artifact = self.read_exported(kind, artifact_id)
        body = self.build_body(artifact, kind)

        self.delete_existing(kind, artifact_id, project_id, body["name"])
        created = self.create_artifact(body, kind, project_id)

        self.record_mapping(kind, artifact_id, created)
        self.archive_exported(kind, artifact_id)
        self.capture_standalone(kind, artifact_id)

        logger.info(
            "Imported %s %s: it is %s in the target", kind, artifact_id, created.get("id")
        )
        return created

    def capture_standalone(self, artifact_type: str, artifact_id: Any) -> Path | None:
        """Capture what the target instance shows for one artifact.

        Only when a browser client was given: the injection is complete
        without one, and the capture is evidence beside it.
        """
        if self._web_client is None:
            logger.debug("No browser client: nothing to capture on the target side")
            return None

        return self._web_client.capture(artifact_type, artifact_id)

    def archive_exported(self, artifact_type: str, artifact_id: Any) -> Path:
        """Move the artifact's file from `exported` to `imported`.

        Which folder holds the file is the record of where the artifact got
        to: still in `exported` means read but not injected, in `imported`
        means it crossed. An earlier copy in `imported` is replaced, so
        running the migration again leaves one file, not two.
        """
        kind = self.resolve_artifact_type(artifact_type)
        name = f"{standalone_file_name(kind, artifact_id)}.json"
        source_file = self._exported_dir / name
        target_file = self._imported_dir / name

        if not source_file.is_file():
            logger.warning("%s is gone: nothing to move to %s", source_file, self._imported_dir)
            return target_file

        self._imported_dir.mkdir(parents=True, exist_ok=True)
        source_file.replace(target_file)
        logger.info("Moved %s to %s: the %s has crossed", name, self._imported_dir, kind)
        return target_file

    def read_exported(self, artifact_type: str, artifact_id: Any) -> dict[str, Any]:
        """Read the artifact the export wrote, by kind and source id."""
        kind = self.resolve_artifact_type(artifact_type)
        source_file = self._exported_dir / f"{standalone_file_name(kind, artifact_id)}.json"
        logger.info("Reading %s", source_file)

        if not source_file.is_file():
            already = self._imported_dir / source_file.name
            if already.is_file():
                logger.error("%s has already been imported: it is in %s", kind, already)
                raise FileNotFoundError(
                    f"{source_file} does not exist because the {kind} has already been "
                    f"imported: its file is now {already}. Export it again to import it again."
                )

            logger.error("%s has not been exported yet", source_file)
            raise FileNotFoundError(
                f"{source_file} does not exist: export the {kind} before importing it"
            )

        try:
            artifact = json.loads(source_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            logger.error("%s is not valid JSON: %s", source_file, error)
            raise ArtifactImportError(f"{source_file} is not valid JSON") from error

        if not isinstance(artifact, dict):
            logger.error("Expected a JSON object in %s, got %s", source_file, type(artifact).__name__)
            raise ArtifactImportError(
                f"{source_file} holds a {type(artifact).__name__}, expected an object"
            )
        return artifact

    def build_body(self, artifact: dict[str, Any], artifact_type: str) -> dict[str, Any]:
        """Reduce an exported artifact to what the target accepts on creation."""
        kind = self.resolve_artifact_type(artifact_type)
        allowed = CREATABLE_FIELDS[kind]

        body = {field: artifact[field] for field in allowed if artifact.get(field) is not None}
        if not body.get("name"):
            logger.error("The exported %s carries no name: %s", kind, sorted(artifact))
            raise ArtifactImportError(f"The exported {kind} must carry a 'name'")

        dropped = sorted(set(artifact) - set(body))
        if dropped:
            logger.info("Not forwarding %s: %s", kind, dropped)
        carried_over = sorted(field for field in dropped if field not in NEVER_FORWARDED)
        if carried_over:
            logger.warning(
                "The target's create endpoint takes none of %s, so those values stay behind",
                carried_over,
            )
        return body

    def create_artifact(
        self,
        body: dict[str, Any],
        artifact_type: str,
        project_id: int,
    ) -> dict[str, Any]:
        """Create the artifact in the target instance and return what came back."""
        kind = self.resolve_artifact_type(artifact_type)
        path = CREATE_PATHS[kind].format(project_id=project_id)
        logger.info("Creating %s %r in project %s", kind, body["name"], project_id)

        response = self._client.post(path, json=body)
        if response.status_code not in SUCCESS_STATUS_CODES:
            logger.error(
                "Target instance rejected the %s %r with status %s: %s",
                kind,
                body["name"],
                response.status_code,
                response.text,
            )
            raise ArtifactImportError(
                f"Could not create the {kind} {body['name']!r}: "
                f"{response.status_code} {response.text}"
            )

        try:
            created = response.json()
        except ValueError as error:
            logger.error("Target instance returned a non-JSON body: %s", response.text)
            raise ArtifactImportError(
                f"Target instance returned a non-JSON response for the {kind} {body['name']!r}"
            ) from error

        if not isinstance(created, dict) or created.get("id") is None:
            logger.error("Response for the %s %r carries no id: %s", kind, body["name"], created)
            raise ArtifactImportError(
                f"Target instance returned no id for the {kind} {body['name']!r}"
            )
        return created

    # -- what an earlier run left behind -------------------------------------

    def delete_existing(
        self,
        artifact_type: str,
        source_id: Any,
        project_id: int,
        name: str,
    ) -> list[Any]:
        """Delete what the target already holds for this artifact, if anything.

        Two ways of finding it, because either can be the one that knows:
        the id map, which says what an earlier run created, and the target's
        own listing by name, for a copy the map never recorded -- a run whose
        map was lost, or an artifact put there by hand.

        Returns the ids deleted.
        """
        kind = self.resolve_artifact_type(artifact_type)
        deleted = []

        for target_id in self.find_existing(kind, source_id, project_id, name):
            if self.delete_artifact(kind, target_id, project_id):
                deleted.append(target_id)

        if deleted:
            logger.warning(
                "Deleted %s %s already in the target (%s) before importing %s again",
                len(deleted),
                f"{kind}s" if len(deleted) > 1 else kind,
                ", ".join(str(item) for item in deleted),
                source_id,
            )
        else:
            logger.info("Nothing to delete: the target holds no %s %r yet", kind, name)
        return deleted

    def find_existing(
        self,
        artifact_type: str,
        source_id: Any,
        project_id: int,
        name: str,
    ) -> list[Any]:
        """Ids the target already holds for this artifact, by map then by name."""
        kind = self.resolve_artifact_type(artifact_type)
        found: list[Any] = []

        recorded = self.target_id_of(kind, source_id)
        if recorded is not None and self.artifact_exists(kind, recorded, project_id):
            logger.info("The id map says %s %s is %s in the target", kind, source_id, recorded)
            found.append(recorded)

        for artifact in self.find_by_name(kind, name, project_id):
            artifact_id = artifact.get("id")
            if artifact_id is not None and artifact_id not in found:
                logger.info("The target already holds a %s named %r: %s", kind, name, artifact_id)
                found.append(artifact_id)

        return found

    def artifact_exists(self, artifact_type: str, target_id: Any, project_id: int) -> bool:
        """Whether the target still holds that artifact: it may have been removed."""
        kind = self.resolve_artifact_type(artifact_type)
        path = f"{CREATE_PATHS[kind].format(project_id=project_id)}/{target_id}"

        response = self._client.get(path)
        if response.status_code == NOT_FOUND:
            logger.info("The %s %s recorded in the id map is gone from the target", kind, target_id)
            return False
        if not response.ok:
            logger.warning(
                "Could not check whether %s %s is still in the target: %s %s",
                kind,
                target_id,
                response.status_code,
                response.text,
            )
            return False
        return True

    def find_by_name(self, artifact_type: str, name: str, project_id: int) -> list[dict[str, Any]]:
        """Artifacts of a kind the target holds under exactly that name."""
        kind = self.resolve_artifact_type(artifact_type)
        if not name:
            return []

        path = CREATE_PATHS[kind].format(project_id=project_id)
        matches = []
        seen: set[Any] = set()

        for page in range(1, MAX_PAGES + 1):
            response = self._client.get(path, params={"page": page, "pageSize": PAGE_SIZE})
            if not response.ok:
                logger.warning(
                    "Could not list the %ss of the target to look for %r: %s %s",
                    kind,
                    name,
                    response.status_code,
                    response.text,
                )
                return matches

            items = self._items_of(response)
            fresh = [item for item in items if item.get("id") not in seen]
            if not fresh:
                break

            seen.update(item.get("id") for item in fresh)
            matches.extend(item for item in fresh if item.get("name") == name)

        return matches

    def delete_artifact(self, artifact_type: str, target_id: Any, project_id: int) -> bool:
        """Remove one artifact from the target instance."""
        kind = self.resolve_artifact_type(artifact_type)
        path = f"{CREATE_PATHS[kind].format(project_id=project_id)}/{target_id}"
        logger.info("Deleting %s %s from the target", kind, target_id)

        response = self._client.delete(path)
        if response.status_code in DELETED_STATUS_CODES:
            return True

        if response.status_code == NOT_FOUND:
            # Already gone: the id map can outlive what it points at, and
            # nothing left to delete is the outcome this was after.
            logger.info(
                "The %s %s was already gone from the target, nothing to delete", kind, target_id
            )
            return False

        logger.error(
            "Could not delete %s %s from the target: %s %s",
            kind,
            target_id,
            response.status_code,
            response.text,
        )
        raise ArtifactImportError(
            f"Could not delete the {kind} {target_id} already in the target: "
            f"{response.status_code} {response.text}. Importing again would duplicate it."
        )

    @staticmethod
    def _items_of(response: Any) -> list[dict[str, Any]]:
        """The artifacts of a listing, whichever shape the endpoint answers in."""
        try:
            payload = response.json()
        except ValueError:
            return []

        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            for key in ITEM_KEYS:
                items = payload.get(key)
                if isinstance(items, list):
                    return [item for item in items if isinstance(item, dict)]
        return []

    # -- what the target called it -------------------------------------------

    def record_mapping(
        self,
        artifact_type: str,
        source_id: Any,
        created: dict[str, Any],
    ) -> Path:
        """Record, against the source id, the id the target assigned.

        The file accumulates: every import adds or replaces its own entry and
        leaves the rest alone, so the map grows into the record of everything
        that crossed.
        """
        kind = self.resolve_artifact_type(artifact_type)
        id_map = self.load_id_map()

        id_map.setdefault(kind, {})[str(source_id)] = {
            "target_id": created.get("id"),
            "name": created.get("name"),
            "imported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

        self._id_map_file.parent.mkdir(parents=True, exist_ok=True)
        with self._id_map_file.open("w", encoding="utf-8", newline="\n") as map_file:
            json.dump(id_map, map_file, indent=2, ensure_ascii=False, sort_keys=True)
            map_file.write("\n")

        logger.info(
            "Recorded %s %s -> %s in %s", kind, source_id, created.get("id"), self._id_map_file
        )
        return self._id_map_file

    def load_id_map(self) -> dict[str, Any]:
        """What has crossed so far, by kind and source id."""
        if not self._id_map_file.is_file():
            return {}

        try:
            id_map = json.loads(self._id_map_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            logger.error("%s is not valid JSON: %s", self._id_map_file, error)
            raise ArtifactImportError(f"{self._id_map_file} is not valid JSON") from error

        return id_map if isinstance(id_map, dict) else {}

    def target_id_of(self, artifact_type: str, source_id: Any) -> Any:
        """The id an artifact was given in the target, or None if it never crossed."""
        kind = self.resolve_artifact_type(artifact_type)
        entry = self.load_id_map().get(kind, {}).get(str(source_id))
        return entry.get("target_id") if entry else None

    @staticmethod
    def resolve_artifact_type(artifact_type: str) -> str:
        """The known artifact kind behind whatever spelling was given."""
        try:
            kind = resolve_artifact_type(artifact_type)
        except UnknownArtifactError as error:
            raise ArtifactImportError(str(error)) from error

        if kind not in CREATE_PATHS:
            raise ArtifactImportError(f"No create endpoint known for a {kind}")
        return kind

    # -- placeholder ---------------------------------------------------------

    def import_project(self) -> int:
        """Import a project into the target instance.

        Placeholder: the project of the proof of concept is created by hand,
        so nothing here creates one. Kept until the migration says what, if
        anything, it needs at project level.
        """
        logger.info("Starting project import into %s", self._client.base_url)
        result = 1
        logger.info("Project import finished, result: %s", result)
        return result
