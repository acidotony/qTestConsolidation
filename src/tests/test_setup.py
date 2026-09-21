"""Smoke test: confirms the project skeleton (src layout + pytest config)
is wired correctly before we add real migration code.
"""

import main


def test_project_is_importable():
    assert main is not None
