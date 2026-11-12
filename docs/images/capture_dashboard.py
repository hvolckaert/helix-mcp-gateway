"""Render the real Dashboard with fictional responses and no network access.

Run from a development environment with the browser-test dependencies and
Playwright Chromium installed. This script never starts a Gateway, reads an
installed configuration, or contacts Helix. Every browser request is intercepted.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
ORIGIN = "http://dashboard.example.invalid"
FORMS = ("Example:ServiceRequest", "Example:Asset", "Example:Change")
FIELDS = (
    {"id": 1, "name": "Request ID", "datatype": "CHAR"},
    {"id": 7, "name": "Status", "datatype": "ENUM"},
    {"id": 8, "name": "Summary", "datatype": "CHAR"},
)


def demo_state() -> dict[str, object]:
    config = yaml.safe_load((ROOT / "config/helix.yaml").read_text())
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"][
        "version"
    ]
    policies = {policy["name"]: policy for policy in config["policies"]}
    policies["dev"].update(
        access_mode="read_write",
        allowed_forms=[FORMS[0]],
        allowed_fields_by_form={FORMS[0]: [field["name"] for field in FIELDS]},
        writable_forms=[FORMS[0]],
        creatable_fields_by_form={FORMS[0]: ["Status", "Summary"]},
        updatable_fields_by_form={FORMS[0]: ["Status", "Summary"]},
    )
    return {
        "server_version": version,
        "revision": "fictional-documentation-revision",
        "dashboard": {"installation_id": "fictional-demo", "process_id": 1},
        "configuration": {
            "server": config["server"],
            "arapi": {
                **config["arapi"],
                "lib_dir": None,
                "detected_lib_dirs": [],
                "java_home": None,
                "detected_java_homes": [],
            },
            "policies": policies,
            "environments": [
                {
                    "environment": environment,
                    "gateway_port": 46262 + index,
                    "policy": environment,
                    "credential": {
                        "configured": environment == "dev",
                        "source": "dotenv" if environment == "dev" else "none",
                        "editable": True,
                    },
                }
                for index, environment in enumerate(("dev", "qa", "prod"))
            ],
        },
        "files": {"configuration": "helix.yaml", "dotenv": ".env"},
        "restart_required": False,
        "local_requirements": {
            "credentials_configured": 1,
            "arapi": "ready",
            "java": "ready",
            "bridge": "ready",
            "kaazing": "ready",
        },
        "kaazing_checks": {"dev": "ready"},
        "update": {
            "managed": True,
            "client": "standalone",
            "release": {
                "status": "current",
                "current_version": version,
                "latest_version": version,
                "update_available": False,
            },
            "operation": None,
        },
        "dashboard_runtime": {
            "installed": True,
            "enabled": True,
            "active": True,
            "manager": "systemd_user",
            "port": 8766,
        },
    }


def main() -> None:
    state = demo_state()
    html = (ROOT / "src/helix_mcp/resources/dashboard/index.html").read_text()
    html = html.replace("__DASHBOARD_TOKEN__", "fictional-documentation-token")
    html = html.replace("__CSP_NONCE__", "fictional-documentation-nonce")
    unexpected: list[str] = []
    errors: list[str] = []

    def respond(route):
        request = route.request
        path = urlsplit(request.url).path
        if request.url == ORIGIN + "/" and request.method == "GET":
            route.fulfill(content_type="text/html; charset=utf-8", body=html)
            return
        if not request.url.startswith(ORIGIN + "/api/"):
            unexpected.append(path)
            route.abort()
            return
        payload = request.post_data_json or {}
        if path == "/api/state" and request.method == "GET":
            result = state
        elif path == "/api/catalog/forms" and request.method == "POST":
            result = {
                "environment": payload["environment"],
                "items": [{"name": name} for name in FORMS],
                "offset": 0,
                "limit": 100,
                "total": len(FORMS),
            }
        elif path == "/api/catalog/fields" and request.method == "POST":
            result = {
                "environment": payload["environment"],
                "form": payload["form"],
                "items": FIELDS,
                "offset": 0,
                "limit": 100,
                "total": len(FIELDS),
            }
        elif path == "/api/capabilities/sql" and request.method == "POST":
            result = {
                "environment": payload["environment"],
                "available": False,
            }
        else:
            unexpected.append(path)
            route.abort()
            return
        route.fulfill(content_type="application/json", body=json.dumps(result))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 1600},
            device_scale_factor=1,
            locale="en-US",
            reduced_motion="reduce",
            service_workers="block",
        )
        context.route("**/*", respond)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(ORIGIN + "/")
        picker = page.locator(
            '[data-form-picker="allowed_forms"][data-policy-env="dev"]'
        )
        expect(picker.locator("[data-form-choice]")).to_have_count(len(FORMS))
        expect(page.locator("#config-status")).to_have_text("Valid")
        page.evaluate("document.fonts.ready")
        assert not unexpected, unexpected
        assert not errors, errors
        output = Path(__file__).with_name("configuration-dashboard.png")
        page.screenshot(path=str(output), animations="disabled")
        context.close()
        browser.close()
    print(
        f"Created {output.name}; all requests served from fictional fixtures."
    )


if __name__ == "__main__":
    main()
