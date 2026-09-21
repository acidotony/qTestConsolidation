"""Export authentication and authorization entities from qTest.

Provides `AuthExporter` which lists users, roles and permissions and
persists them as JSON and CSV for inspection.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Any

from main.client import RestClient
from main.exporter.data_exporter import PAGE_SIZE, MAX_PAGES, ITEM_KEYS
from main.writer.file_writer import FileWriter, IMPORTED_DIR

logger = logging.getLogger(__name__)

# Assumed endpoints for authentication/authorization information.
USERS_PATH = "/api/v3/users"
ROLES_PATH = "/api/v3/roles"
PERMISSIONS_PATH = "/api/v3/permissions"


class AuthExportError(RuntimeError):
    """Raised when an auth-related export fails."""


class AuthExporter:
    """Fetches users, roles and permissions from a qTest instance.

    It intentionally mirrors the simple, robust pagination used by
    `DataExporter.fetch_all` so it can run against large instances.
    """

    def __init__(self, client: RestClient, output_dir: Path | str = IMPORTED_DIR) -> None:
        self._client = client
        self._output_dir = Path(output_dir)
        logger.debug("Auth exporter ready for %s, output=%s", client.base_url, self._output_dir)

    @property
    def client(self) -> RestClient:
        return self._client

    @property
    def output_dir(self) -> Path:
        return self._output_dir

    def _items_of(self, payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, dict):
            for key in ITEM_KEYS:
                if key in payload and isinstance(payload[key], list):
                    return payload[key]
            # Fallback: if the payload itself looks like a list carrier, return empty
            return []
        if isinstance(payload, list):
            return payload
        return []

    def _fetch_all(self, path: str, subject: str) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        seen: set[Any] = set()

        for page in range(1, MAX_PAGES + 1):
            logger.info("Fetching %s, page %s", subject, page)
            response = self._client.get(path, params={"page": page, "pageSize": PAGE_SIZE})
            try:
                payload = response.json()
            except Exception:
                logger.exception("Failed to parse JSON for %s", subject)
                raise AuthExportError(f"Invalid JSON when reading {subject}")

            items = self._items_of(payload)
            if not items:
                logger.debug("Page %s of %s is empty, stopping", page, subject)
                break

            fresh = [item for item in items if self._key_of(item) not in seen]
            if not fresh:
                logger.warning("Page %s of %s repeated entities already seen, stopping", page, subject)
                break

            seen.update(self._key_of(item) for item in fresh)
            collected.extend(fresh)

        logger.info("Collected %s %s", len(collected), subject)
        return collected

    @staticmethod
    def _key_of(item: dict[str, Any]) -> Any:
        return item.get("id") or item.get("userId") or str(item)

    def fetch_users(self) -> list[dict[str, Any]]:
        """Return all users visible to the token."""
        return self._fetch_all(USERS_PATH, "users")

    def fetch_roles(self) -> list[dict[str, Any]]:
        """Return all roles defined in the instance."""
        return self._fetch_all(ROLES_PATH, "roles")

    def fetch_permissions(self) -> list[dict[str, Any]]:
        """Return all permission entries (if the API exposes them)."""
        return self._fetch_all(PERMISSIONS_PATH, "permissions")

    def _write_json(self, payload: Any, file_name: str) -> Path:
        return FileWriter(payload, output_dir=self._output_dir).write(file_name)

    def _write_csv(self, items: list[dict[str, Any]], file_name: str) -> Path:
        self._output_dir.mkdir(parents=True, exist_ok=True)
        target = self._output_dir / f"{file_name}.csv"

        # Union of all keys to form CSV columns
        keys = set()
        for item in items:
            if isinstance(item, dict):
                keys.update(item.keys())
        headers = sorted(keys) or ["id"]

        with target.open("w", encoding="utf-8-sig", newline="") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=headers, extrasaction="ignore")
            writer.writeheader()
            for item in items:
                writer.writerow({k: (v if v is not None else "") for k, v in (item or {}).items()})

        logger.info("Wrote CSV %s (%s rows)", target, len(items))
        return target

    def export_all(self) -> dict[str, Path]:
        """Fetch users, roles and permissions and write them as JSON and CSV.

        Returns a mapping of artifact name to written Path.
        """
        written: dict[str, Path] = {}

        users = self.fetch_users()
        written["users_json"] = self._write_json(users, "qtest_users")
        written["users_csv"] = self._write_csv(users, "qtest_users")

        roles = self.fetch_roles()
        written["roles_json"] = self._write_json(roles, "qtest_roles")
        written["roles_csv"] = self._write_csv(roles, "qtest_roles")

        try:
            permissions = self.fetch_permissions()
        except AuthExportError:
            permissions = []

        if permissions:
            written["permissions_json"] = self._write_json(permissions, "qtest_permissions")
            written["permissions_csv"] = self._write_csv(permissions, "qtest_permissions")

        return written
