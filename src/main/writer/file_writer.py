"""Persistence of extracted test data as JSON files.

`FileWriter` takes a JSON object and writes it to the project's
`migration/imported/` folder, where it waits to be injected into the target
instance. It performs no HTTP work: exporters hand it the data they already
retrieved.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Project folder holding payloads pending injection into the target instance.
IMPORTED_DIR = Path(__file__).resolve().parents[3] / "migration" / "imported"

#: Project folder holding what has been read out of the source instance.
EXPORTED_DIR = Path(__file__).resolve().parents[3] / "migration" / "exported"

JSON_EXTENSION = ".json"
JSON_INDENT = 2


class FileWriter:
    """Writes a JSON payload to a `.json` file under `migration/imported/`.

    Example:
        >>> writer = FileWriter({"name": "Sample project"})
        >>> writer.write("project")
        WindowsPath('.../migration/imported/project.json')
    """

    def __init__(self, data: Any, output_dir: Path | str = IMPORTED_DIR) -> None:
        self.data = data
        self._output_dir = Path(output_dir)
        logger.debug("Writer ready, output folder: %s", self._output_dir)

    # -- payload -------------------------------------------------------------

    @property
    def data(self) -> Any:
        """The JSON payload to be written: a parsed object, not a JSON string."""
        return self._data

    @data.setter
    def data(self, value: Any) -> None:
        if value is None:
            logger.error("Cannot set the payload: data is missing")
            raise ValueError("data is required")
        logger.debug("Payload set: %s with %s top-level items", type(value).__name__, _size_of(value))
        self._data = value

    @property
    def output_dir(self) -> Path:
        """Folder the payload is written to."""
        return self._output_dir

    # -- persistence ---------------------------------------------------------

    def write(self, file_name: str, overwrite: bool = True) -> Path:
        """Write the payload as JSON and return the resulting path.

        Args:
            file_name: name of the target file. The `.json` extension is added
                when missing.
            overwrite: replace the file when it already exists. On by default;
                pass False to protect a payload that has not been injected yet.

        Raises:
            ValueError: if `file_name` is empty.
            FileExistsError: if the file exists and `overwrite` is False.
            TypeError: if the payload is not JSON-serializable.
        """
        target = self._resolve_path(file_name)

        if target.exists():
            if not overwrite:
                logger.error("%s already exists and overwrite is disabled", target)
                raise FileExistsError(
                    f"{target} already exists. Pass overwrite=True to replace it."
                )
            logger.warning("Overwriting existing file %s", target)

        self._output_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Writing payload to %s", target)
        try:
            with target.open("w", encoding="utf-8", newline="\n") as json_file:
                json.dump(self._data, json_file, indent=JSON_INDENT, ensure_ascii=False)
                json_file.write("\n")
        except (TypeError, OSError):
            logger.exception("Failed to write payload to %s", target)
            raise

        logger.info("Wrote %s (%s bytes)", target.name, target.stat().st_size)
        return target

    def _resolve_path(self, file_name: str) -> Path:
        name = (file_name or "").strip()
        if not name:
            logger.error("Cannot resolve the target path: file_name is missing")
            raise ValueError("file_name is required")
        if not name.lower().endswith(JSON_EXTENSION):
            logger.debug("Adding the %s extension to %s", JSON_EXTENSION, name)
            name = f"{name}{JSON_EXTENSION}"
        target = self._output_dir / name
        logger.debug("Target path resolved to %s", target)
        return target


def _size_of(value: Any) -> int | str:
    """Item count of a payload, for logging purposes only."""
    try:
        return len(value)
    except TypeError:
        return "n/a"
