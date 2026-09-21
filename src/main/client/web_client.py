"""Browser client that captures what a qTest instance shows for an artifact.

The REST calls say what the migration did; a screenshot of each instance says
what a person would see. `WebClient` opens the page of one artifact and saves
it, so every migrated artifact has a before in HS and an after in HCSC.

Nothing about an instance is written here. The address of each artifact's page
differs between qTest versions and modules, so the URL of every kind is a
setting in `config/qtest.env`, alongside the login page, the credentials and
how to run the browser.

A capture is evidence, not the migration itself: when the browser cannot
start, or the page cannot be reached, the failure is reported and the
migration carries on. Set `<PREFIX>_QTEST_WEB_REQUIRED=true` to make it fatal
instead.

Captures are saved as PNG, which is what the browser hands over.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from main.artifacts import file_label, resolve_artifact_type

logger = logging.getLogger(__name__)

#: Where the captures are written, next to the rest of the tests' output.
SCREENSHOT_DIR = Path(__file__).resolve().parents[3] / "src" / "tests" / "output" / "screenshots"

#: Default location of the connection settings file (project root: config/).
DEFAULT_ENV_FILE = Path(__file__).resolve().parents[3] / "config" / "qtest.env"

#: Suffix shared with the exported files, so a capture and the JSON it
#: evidences are named after the same artifact.
STANDALONE_SUFFIX = "stand-alone"

#: PNG, because that is what the browser hands over. Converting to anything
#: else would buy a smaller file at the cost of a dependency.
CAPTURE_EXTENSION = ".png"

DEFAULT_WINDOW_SIZE = "1920,1080"
DEFAULT_PAGE_WAIT = 5

#: Seconds allowed after submitting the credentials, for the instance to let
#: the browser in and draw its first page.
DEFAULT_LOGIN_WAIT = 15

#: How the sign-in fields are found. Their names change between qTest
#: versions, so they are found by what they are: the box that takes a name or
#: an address, and the one that hides what is typed.
DEFAULT_USER_SELECTOR = (
    "input[type='email'], input[name*='user' i], input[id*='user' i], input[type='text']"
)
DEFAULT_PASSWORD_SELECTOR = "input[type='password']"


class WebCaptureError(RuntimeError):
    """Raised when a capture was required and could not be taken."""


class WebClient:
    """Opens the page of one artifact in a qTest instance and captures it."""

    def __init__(
        self,
        base_url: str,
        side: str,
        url_templates: dict[str, str] | None = None,
        screenshot_dir: Path | str = SCREENSHOT_DIR,
        project_id: Any = "",
        username: str | None = None,
        password: str | None = None,
        login_url: str = "",
        user_selector: str = DEFAULT_USER_SELECTOR,
        password_selector: str = DEFAULT_PASSWORD_SELECTOR,
        profile_dir: str | None = None,
        headless: bool = True,
        window_size: str = DEFAULT_WINDOW_SIZE,
        page_wait: int = DEFAULT_PAGE_WAIT,
        login_wait: int = DEFAULT_LOGIN_WAIT,
        required: bool = False,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.side = side
        self.project_id = project_id
        self._url_templates = url_templates or {}
        self._screenshot_dir = Path(screenshot_dir)
        self._username = username
        self._password = password
        self._login_url = (login_url or "").strip()
        self._user_selector = user_selector
        self._password_selector = password_selector
        self._profile_dir = profile_dir
        self._headless = headless
        self._window_size = window_size
        self._page_wait = page_wait
        self._login_wait = login_wait
        self._required = required
        # Started on the first capture: a migration that captures nothing
        # should not pay for a browser.
        self._driver: Any = None

        logger.debug(
            "Web client ready for the %s instance at %s, capturing into %s",
            side,
            self.base_url,
            self._screenshot_dir,
        )

    # -- construction --------------------------------------------------------

    @classmethod
    def from_env(
        cls,
        side: str,
        env_file: Path | str = DEFAULT_ENV_FILE,
        **overrides: Any,
    ) -> WebClient:
        """Build a client for one instance from the `.env` settings.

        Args:
            side: `source` for HS, `target` for HCSC. It picks the prefix of
                the settings and names the files the captures are written to.
        """
        prefix = side.upper()
        values = {**dotenv_values(Path(env_file)), **os.environ}

        def setting(name: str, default: str = "") -> str:
            return (values.get(f"{prefix}_QTEST_{name}") or default).strip()

        templates = {
            kind: setting(f"WEB_URL_{kind.replace('-', '_').upper()}")
            for kind in ("requirement", "test-case", "test-run", "test-cycle", "test-suite",
                         "release", "module")
        }
        templates = {kind: url for kind, url in templates.items() if url}
        fallback = setting("WEB_URL_TEMPLATE")
        if fallback:
            templates.setdefault("*", fallback)

        return cls(
            base_url=setting("BASE_URL"),
            side=side,
            url_templates=templates,
            project_id=setting("PROJECT_ID"),
            username=setting("WEB_USERNAME") or None,
            password=setting("WEB_PASSWORD") or None,
            login_url=setting("WEB_LOGIN_URL"),
            user_selector=setting("WEB_USER_SELECTOR") or DEFAULT_USER_SELECTOR,
            password_selector=setting("WEB_PASSWORD_SELECTOR") or DEFAULT_PASSWORD_SELECTOR,
            profile_dir=setting("BROWSER_PROFILE") or None,
            headless=setting("BROWSER_HEADLESS", "true").lower() != "false",
            window_size=setting("BROWSER_WINDOW_SIZE", DEFAULT_WINDOW_SIZE),
            page_wait=int(setting("WEB_PAGE_WAIT", str(DEFAULT_PAGE_WAIT)) or DEFAULT_PAGE_WAIT),
            login_wait=int(setting("WEB_LOGIN_WAIT", str(DEFAULT_LOGIN_WAIT)) or DEFAULT_LOGIN_WAIT),
            required=setting("WEB_REQUIRED", "false").lower() == "true",
            **overrides,
        )

    # -- capturing -----------------------------------------------------------

    def capture(self, artifact_type: str, artifact_id: Any) -> Path | None:
        """Open the page of one artifact and save what it shows.

        Returns the file written, or None when the capture could not be taken
        and captures are not required.
        """
        kind = resolve_artifact_type(artifact_type)
        target = self.capture_path(kind, artifact_id)
        url = self.artifact_url(kind, artifact_id)

        if not url:
            return self._give_up(
                f"no page URL configured for a {kind} of the {self.side} instance: "
                f"set {self.side.upper()}_QTEST_WEB_URL_{kind.replace('-', '_').upper()} "
                f"or {self.side.upper()}_QTEST_WEB_URL_TEMPLATE"
            )

        logger.info("Capturing the %s %s at %s", self.side, kind, url)
        try:
            driver = self._browser()
            driver.get(url)
            self._settle(driver)
            image = driver.get_screenshot_as_png()
        except Exception as error:  # noqa: BLE001 - any browser failure is the same to us
            return self._give_up(f"{type(error).__name__}: {error}")

        # Written over any earlier capture: the migration runs more than once.
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(image)

        logger.info("Captured the %s %s %s to %s", self.side, kind, artifact_id, target)
        return target

    def capture_path(self, artifact_type: str, artifact_id: Any) -> Path:
        """Where the capture of one artifact goes, named after the artifact."""
        kind = resolve_artifact_type(artifact_type)
        name = f"{self.side}_{file_label(kind)}_{artifact_id}_{STANDALONE_SUFFIX}{CAPTURE_EXTENSION}"
        return self._screenshot_dir / name

    def artifact_url(self, artifact_type: str, artifact_id: Any) -> str:
        """The page of one artifact, from the template configured for its kind."""
        kind = resolve_artifact_type(artifact_type)
        template = self._url_templates.get(kind) or self._url_templates.get("*", "")
        if not template:
            return ""

        return template.format(
            base_url=self.base_url,
            project_id=self.project_id,
            artifact_id=artifact_id,
            artifact_type=kind,
        )

    def _give_up(self, reason: str) -> None:
        """Report a capture that could not be taken, or raise if it was required."""
        if self._required:
            logger.error("Capture of the %s instance failed: %s", self.side, reason)
            raise WebCaptureError(f"Capture of the {self.side} instance failed: {reason}")

        logger.warning(
            "No capture of the %s instance (%s). The migration itself is unaffected.",
            self.side,
            reason,
        )
        return None

    # -- the browser ---------------------------------------------------------

    def _browser(self) -> Any:
        """The browser, started on first use and kept for later captures."""
        if self._driver is not None:
            return self._driver

        from selenium import webdriver

        options = webdriver.ChromeOptions()
        if self._headless:
            options.add_argument("--headless=new")
        options.add_argument(f"--window-size={self._window_size}")
        if self._profile_dir:
            # A profile signed in by hand once is what makes this work behind
            # single sign-on, where a scripted login cannot go.
            options.add_argument(f"--user-data-dir={self._profile_dir}")

        logger.info("Starting the browser for the %s instance", self.side)
        self._driver = webdriver.Chrome(options=options)
        self._driver.set_page_load_timeout(60)

        if self._username and self._password:
            self._sign_in(self._driver)
        return self._driver

    def _sign_in(self, driver: Any) -> None:
        """Sign in on the instance's own login page.

        Both instances ask for a network user and a password on one page, with
        no redirect to an identity provider and no second factor, so the two
        fields are filled and the form submitted from the password box.

        The fields are found by what they are -- the box that takes a name,
        then the one that hides what is typed -- rather than by names that
        change between qTest versions; `WEB_USER_SELECTOR` and
        `WEB_PASSWORD_SELECTOR` override that if ever needed. A form that
        asks for the password on a second page is handled too.

        Failing to sign in is reported, not raised: the session may already be
        open in the browser profile, and a capture that lands on a login page
        shows that plainly enough.
        """
        login_url = self._login_url or self.base_url
        logger.info("Signing in to the %s instance at %s as %s", self.side, login_url, self._username)

        try:
            driver.get(login_url)
            self._settle(driver)

            if not self._fill(driver, self._user_selector, self._username, "user name"):
                logger.info(
                    "No sign-in form on the %s instance: the session looks to be open already",
                    self.side,
                )
                return

            if not self._fill(driver, self._password_selector, self._password, "password", True):
                # Some forms only show the password once the user is submitted.
                logger.debug("No password field yet: submitting the user name first")
                self._press_enter(driver, self._user_selector)
                self._settle(driver)
                self._fill(driver, self._password_selector, self._password, "password", True)

            self._wait(self._login_wait)
            logger.info("Signed in to the %s instance, now at %s", self.side, driver.current_url)
        except Exception as error:  # noqa: BLE001 - a login page can present anything
            logger.warning(
                "Could not sign in to the %s instance (%s: %s). Carrying on: the session may "
                "already be open, and a capture that fails says so on its own.",
                self.side,
                type(error).__name__,
                error,
            )

    def _fill(
        self,
        driver: Any,
        selector: str,
        value: str,
        what: str,
        submit: bool = False,
    ) -> bool:
        """Type a value into the first field the selector finds."""
        field = self._field(driver, selector)
        if field is None:
            logger.debug("No %s field matching %r on this page", what, selector)
            return False

        from selenium.webdriver.common.keys import Keys

        logger.debug("Entering the %s", what)
        field.clear()
        field.send_keys(value)
        if submit:
            field.send_keys(Keys.ENTER)
        return True

    def _press_enter(self, driver: Any, selector: str) -> None:
        """Submit the form from the field the selector finds."""
        from selenium.webdriver.common.keys import Keys

        field = self._field(driver, selector)
        if field is not None:
            field.send_keys(Keys.ENTER)

    @staticmethod
    def _field(driver: Any, selector: str) -> Any:
        """The first field a person could actually type into, or None."""
        from selenium.webdriver.common.by import By

        for field in driver.find_elements(By.CSS_SELECTOR, selector):
            if field.is_displayed() and field.is_enabled():
                return field
        return None

    def _settle(self, driver: Any) -> None:
        """Give the page the configured moment to finish drawing itself."""
        self._wait(self._page_wait)

    @staticmethod
    def _wait(seconds: int) -> None:
        import time

        time.sleep(seconds)

    # -- lifetime ------------------------------------------------------------

    def close(self) -> None:
        """Close the browser, if one was ever started."""
        if self._driver is None:
            return

        logger.debug("Closing the browser for the %s instance", self.side)
        try:
            self._driver.quit()
        except Exception as error:  # noqa: BLE001 - closing must never break a run
            logger.warning("The browser did not close cleanly: %s", error)
        finally:
            self._driver = None

    def __enter__(self) -> WebClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
