"""Real dashboard HTML/HTTP with temporary policy and fictional metadata."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from helix_mcp.clients.arapi import ArapiField
from helix_mcp.dashboard import DashboardHTTPServer, DashboardService

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.dashboard_browser,
    pytest.mark.skipif(
        os.environ.get("HELIX_DASHBOARD_BROWSER_TESTS") != "1",
        reason="set HELIX_DASHBOARD_BROWSER_TESTS=1 for Chromium tests",
    ),
]
ROOT = Path(__file__).resolve().parents[2]
NEW_FORM = "Example:AfterUpdate"
DRAFT_PASSWORD = "fictional-unsaved-password"


class BrowserDashboard:
    def __init__(self, path: Path) -> None:
        config = path / "helix.yaml"
        config.write_bytes((ROOT / "config/helix.yaml").read_bytes())
        self.dotenv = path / ".env"
        self.dotenv.write_text(
            f"HELIX_CONFIG_PATH={json.dumps(str(config))}\n"
            'HELIX_CREDENTIAL_DEV=\'{"username":"example",'
            '"password":"fictional-saved-password"}\'\n',
            encoding="utf-8",
        )
        self.dotenv.chmod(0o600)
        self.generation = 1
        self.forms = ["Example:ReadableForm", "Example:DraftForm"]
        self.saved_requests = []
        self.catalog_calls = 0
        self.update_started = False
        self.start(0)

    def start(self, port: int) -> None:
        service = DashboardService(self.dotenv, process_environment={})
        self.service = service
        service._metadata_session = SimpleNamespace(
            list_forms=self.list_forms,
            list_fields=lambda *args, **kwargs: (
                ArapiField(id=1, name="Request ID", datatype="CHAR"),
                ArapiField(id=7, name="Status", datatype="ENUM"),
            ),
            check_sql_access=lambda *args, **kwargs: True,
            close=lambda: None,
        )
        original_state = service.state
        original_identity = service.identity
        original_configure = service.configure

        def identity():
            return {**original_identity(), "process_id": self.generation}

        def state():
            result = original_state()
            result["server_version"] = f"1.0.{self.generation - 1}"
            result["update"] = {
                "managed": True,
                "client": "standalone",
                "release": {
                    "status": "available",
                    "latest_version": "1.0.1",
                    "update_available": self.generation == 1,
                },
                "operation": (
                    {"status": "success", "target_version": "1.0.1"}
                    if self.generation > 1
                    else None
                ),
            }
            return result

        def configure(payload):
            self.saved_requests.append(payload)
            return original_configure(payload)

        def start_update(**kwargs):
            self.update_started = True
            return {"status": "queued", "target_version": "1.0.1"}

        service.identity = identity
        service.state = state
        service.configure = configure
        service.start_update = start_update
        self.server = DashboardHTTPServer(
            ("127.0.0.1", port), service, f"fictional-token-{self.generation}"
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def list_forms(self, *args, **kwargs):
        self.catalog_calls += 1
        return tuple(self.forms)

    def restart(self):
        port = self.server.server_port
        self.close()
        self.generation += 1
        self.forms.append(NEW_FORM)
        self.start(port)

    def close(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()


@pytest.fixture
def dashboard(tmp_path):
    app = BrowserDashboard(tmp_path)
    try:
        yield app
    finally:
        app.close()


@pytest.fixture
def page():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context()
        page = context.new_page()
        page.set_default_timeout(8000)
        try:
            yield page
        finally:
            context.close()
            browser.close()


def picker(page):
    return page.locator(
        '[data-form-picker="allowed_forms"][data-policy-env="dev"]'
    )


def edit_draft(page):
    from playwright.sync_api import expect

    expect(picker(page).locator("[data-form-choice]")).to_have_count(2)
    page.locator("#tab-advanced").click()
    page.locator("#metadata-ttl").fill("2345")
    page.locator("#tab-qa").click()
    page.locator("#max_rows-qa").fill("7")
    page.locator("#tab-dev").click()
    page.locator("#max_rows-dev").fill("42")
    picker(page).locator('[data-form-choice="Example:DraftForm"]').check()
    page.locator("#access_mode-dev").select_option("read_write")
    page.locator(
        '[data-form-picker="writable_forms"][data-policy-env="dev"]'
    ).locator('[data-form-choice="Example:DraftForm"]').check()
    for field in ("creatable_fields_by_form", "updatable_fields_by_form"):
        page.locator(
            f'[data-field-mapping="{field}"][data-policy-env="dev"]'
        ).locator('[data-field-choice="Status"]').check()
    page.locator('[data-replace="dev"]').check()
    page.locator("#username-dev").fill("fictional-draft-user")
    page.locator("#password-dev").fill(DRAFT_PASSWORD)


def assert_draft(page):
    from playwright.sync_api import expect

    expect(page.locator("#metadata-ttl")).to_have_value("2345")
    expect(page.locator("#max_rows-dev")).to_have_value("42")
    expect(page.locator("#max_rows-qa")).to_have_value("7")
    expect(page.locator("#writable_forms-dev")).to_have_value(
        "Example:DraftForm"
    )
    expect(page.locator("#password-dev")).to_have_value(DRAFT_PASSWORD)
    expect(page.locator("#allowed_forms-dev")).to_contain_text(
        "Example:ReadableForm"
    )
    assert (
        "Example:DraftForm" in page.locator("#allowed_forms-dev").input_value()
    )
    expect(page.locator("#review-button")).to_be_enabled()
    stored = page.evaluate(
        "JSON.stringify([Object.entries(localStorage), Object.entries(sessionStorage)])"
    )
    assert DRAFT_PASSWORD not in stored


def test_expired_token_recovers_selector_without_losing_draft(page, dashboard):
    from playwright.sync_api import expect

    page.goto(dashboard.url)
    edit_draft(page)
    dashboard.restart()
    picker(page).locator("[data-form-refresh]").click()
    expect(
        picker(page).locator(f'[data-form-choice="{NEW_FORM}"]')
    ).to_be_visible()
    assert_draft(page)
    assert dashboard.saved_requests == []


def test_failed_recovery_can_be_retried_without_reloading(page, dashboard):
    from playwright.sync_api import expect

    page.goto(dashboard.url)
    edit_draft(page)
    dashboard.restart()
    page.route("**/api/session", lambda route: route.fulfill(status=503))
    picker(page).locator("[data-form-refresh]").click()
    expect(page.locator("#reconnect-session")).to_be_visible()
    expect(page.locator("#session-message")).to_contain_text("Keep it open")
    assert_draft(page)
    page.unroute("**/api/session")
    page.locator("#reconnect-session").click()
    expect(
        picker(page).locator(f'[data-form-choice="{NEW_FORM}"]')
    ).to_be_visible()
    assert_draft(page)


def test_expired_save_is_dispatched_once_after_token_recovery(page, dashboard):
    from playwright.sync_api import expect

    page.goto(dashboard.url)
    edit_draft(page)
    dashboard.restart()
    page.locator("#review-button").click()
    page.locator("#confirm-save").click()
    expect(page.locator("#review-button")).to_be_disabled()
    assert len(dashboard.saved_requests) == 1
    saved = dashboard.saved_requests[0]
    assert saved["credentials"]["dev"]["password"] == DRAFT_PASSWORD
    assert saved["server"]["metadata_cache_ttl_seconds"] == 2345


def test_lost_save_response_is_not_replayed(page, dashboard):
    from playwright.sync_api import expect

    page.goto(dashboard.url)
    edit_draft(page)

    def lose_response(route):
        response = route.fetch()
        assert response.status == 200
        route.abort()

    page.route("**/api/configuration", lose_response)
    page.locator("#review-button").click()
    page.locator("#confirm-save").click()
    expect(page.locator("#toast")).to_contain_text("Failed to fetch")
    assert len(dashboard.saved_requests) == 1
    assert_draft(page)


def test_restart_preserves_original_revision_and_rejects_stale_save(
    page, dashboard
):
    from playwright.sync_api import expect

    page.goto(dashboard.url)
    edit_draft(page)
    page.on("dialog", lambda dialog: dialog.accept())
    with page.expect_response("**/api/update/install"):
        page.locator("#install-update-button").click()
    config = dashboard.dotenv.parent / "helix.yaml"
    config.write_text(config.read_text() + "\n# Concurrent external change\n")
    expected_config = config.read_bytes()
    dashboard.restart()
    expect(page.locator("#server-version")).to_have_text("Server 1.0.1")
    expect(page.locator("#session-message")).to_contain_text(
        "saved configuration changed"
    )
    expect(
        picker(page).locator(f'[data-form-choice="{NEW_FORM}"]')
    ).to_be_visible()
    page.locator("#review-button").click()
    page.locator("#confirm-save").click()
    expect(page.locator("#toast")).to_contain_text("configuration changed")
    assert config.read_bytes() == expected_config
    assert_draft(page)


def test_different_installation_does_not_receive_retried_edits(
    page, dashboard
):
    from playwright.sync_api import expect

    page.goto(dashboard.url)
    edit_draft(page)
    dashboard.restart()
    original_identity = dashboard.service.identity
    dashboard.service.identity = lambda: {
        **original_identity(),
        "installation_id": "another-installation",
    }
    page.locator("#review-button").click()
    page.locator("#confirm-save").click()
    expect(page.locator("#session-message")).to_contain_text(
        "different Gateway installation"
    )
    assert dashboard.saved_requests == []
    assert_draft(page)


def test_concurrent_rejections_share_one_session_renewal(page, dashboard):
    from playwright.sync_api import expect

    page.goto(dashboard.url)
    edit_draft(page)
    renewals = []
    page.on(
        "request",
        lambda request: (
            renewals.append(request.url)
            if request.url.endswith("/api/session")
            else None
        ),
    )
    dashboard.restart()
    page.evaluate("""() => {
      document.querySelector('[data-form-picker="allowed_forms"][data-policy-env="dev"] [data-form-refresh]').click();
      document.querySelector('[data-field-mapping="creatable_fields_by_form"][data-policy-env="dev"] [data-field-refresh]').click();
    }""")
    expect(
        picker(page).locator(f'[data-form-choice="{NEW_FORM}"]')
    ).to_be_visible()
    expect(
        page.locator(
            '[data-field-mapping="creatable_fields_by_form"][data-policy-env="dev"] [data-field-status]'
        )
    ).to_contain_text("2 fields")
    assert len(renewals) == 1
    assert_draft(page)


def test_second_token_rejection_stops_after_one_retry(page, dashboard):
    from playwright.sync_api import expect

    page.goto(dashboard.url)
    edit_draft(page)
    requests = []

    def reject(route):
        requests.append(route.request.url)
        route.fulfill(
            status=403,
            json={
                "error": "invalid dashboard token",
                "error_code": "DASHBOARD_TOKEN_INVALID",
            },
        )

    page.route("**/api/catalog/forms", reject)
    picker(page).locator("[data-form-refresh]").click()
    expect(page.locator("#session-message")).to_contain_text("changed again")
    expect(page.locator("#reconnect-session")).to_be_visible()
    assert len(requests) == 2
    assert_draft(page)


@pytest.mark.parametrize("status", [403, 500])
def test_other_failures_do_not_renew_or_repeat_requests(
    page, dashboard, status
):
    from playwright.sync_api import expect

    page.goto(dashboard.url)
    edit_draft(page)
    requests = []
    renewals = []
    page.on(
        "request",
        lambda request: (
            renewals.append(request.url)
            if request.url.endswith("/api/session")
            else None
        ),
    )

    def reject(route):
        requests.append(route.request.url)
        route.fulfill(status=status, json={"error": "A different failure"})

    page.route("**/api/catalog/forms", reject)
    picker(page).locator("[data-form-refresh]").click()
    expect(picker(page).locator("[data-catalog-status]")).to_contain_text(
        "A different failure"
    )
    assert len(requests) == 1 and renewals == []
    assert_draft(page)


def test_update_keeps_incomplete_values_and_active_tab(page, dashboard):
    from playwright.sync_api import expect

    page.goto(dashboard.url)
    edit_draft(page)
    page.locator("#tab-advanced").click()
    page.locator("#metadata-ttl").fill("")
    page.on("dialog", lambda dialog: dialog.accept())
    with page.expect_response("**/api/update/install"):
        page.locator("#install-update-button").click()
    dashboard.restart()
    expect(page.locator("#server-version")).to_have_text("Server 1.0.1")
    expect(page.locator("#metadata-ttl")).to_have_value("")
    expect(page.locator("#tab-advanced")).to_have_attribute(
        "aria-selected", "true"
    )
    expect(page.locator("#password-dev")).to_have_value(DRAFT_PASSWORD)
    page.locator("#tab-dev").click()
    expect(
        picker(page).locator(f'[data-form-choice="{NEW_FORM}"]')
    ).to_be_visible()
    expect(
        page.locator(
            '[data-form-picker="writable_forms"][data-policy-env="dev"] [data-form-choice="Example:DraftForm"]'
        )
    ).to_be_checked()
    assert dashboard.saved_requests == []


@pytest.mark.parametrize("with_draft", [False, True])
def test_selector_after_managed_update(page, dashboard, with_draft):
    from playwright.sync_api import expect

    page.goto(dashboard.url)
    expect(picker(page).locator("[data-form-choice]")).to_have_count(2)
    if with_draft:
        edit_draft(page)
    page.on("dialog", lambda dialog: dialog.accept())
    with page.expect_response("**/api/update/install"):
        page.locator("#install-update-button").click()
    assert dashboard.update_started
    dashboard.restart()
    expect(page.locator("#server-version")).to_have_text("Server 1.0.1")
    expect(
        picker(page).locator(f'[data-form-choice="{NEW_FORM}"]')
    ).to_be_visible()
    if with_draft:
        assert_draft(page)
    picker(page).locator(f'[data-form-choice="{NEW_FORM}"]').check()
    assert NEW_FORM in page.locator("#allowed_forms-dev").input_value()
    if with_draft:
        page.locator(
            '[data-form-picker="writable_forms"][data-policy-env="dev"]'
        ).locator(f'[data-form-choice="{NEW_FORM}"]').check()
        assert NEW_FORM in page.locator("#writable_forms-dev").input_value()
    assert dashboard.saved_requests == []
