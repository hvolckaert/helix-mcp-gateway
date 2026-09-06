# Local configuration dashboard

The administrative dashboard is the normal configuration interface for a local
Helix MCP Gateway installation. `helix-mcp-setup` launches it as a detached
process, opens the browser, and reports its process ID and URL. It remains
independent of the MCP transport and listens only on loopback:

```text
http://127.0.0.1:8766/
```

If setup finds the same installation already serving that address, it reuses
the existing process instead of starting a duplicate. A different service or
Helix MCP installation on the port is rejected safely.

For manual operation, `helix-mcp-dashboard --dotenv /path/to/.env` starts the
dashboard in the foreground. Use `--port` to select a different loopback port
or `--no-browser` when it must not open a browser automatically. Setup accepts
`--no-dashboard` for headless or unattended installation.

## Editable settings

The dashboard can:

- edit the operational parameters in the fixed DEV, QA, and PROD access
  policies;
- browse the forms and fields visible to each saved environment credential;
- configure the loopback AR API bridge URL, timeout, and pool size;
- configure bounded cache and write-plan settings;
- replace a DEV, QA, or PROD credential;
- run sanitized installation and live environment checks;
- check for and explicitly install verified stable server releases.

Each environment owns exactly one policy with the canonical internal name
`dev`, `qa`, or `prod`; the assignment cannot be changed. Older policy names are
migrated to these names on the first successful dashboard save. All three start
with writes disabled (`read_only` internally), but an administrator can enable
the same controlled `read_write` workflow for any environment, including PROD.
The dashboard labels this setting **Write access** because form reads are always
enabled in dashboard-managed policies. Reviewed SQL reads remain optional.
Human approval, a non-empty reason, and complete create/update allowlists are
mandatory whenever writes are enabled. These guarantees are not exposed as
optional dashboard controls.

The low-level `allow_form_reads` setting remains part of the YAML contract for
specialized headless policies and compatibility. The dashboard does not expose
it and writes `allow_form_reads: true` for DEV, QA, and PROD whenever settings
are saved.

Controls follow their effective policy dependencies in the dashboard:

- read limits and form permissions remain available because form reads are a
  core gateway capability;
- SQL object controls require reviewed SQL reads and a saved credential with
  AR System administrator permission; and
- write rate and write-scope controls require controlled writes.

The environment view groups these dependencies by resource. **Form
permissions** contains the shared form boundary plus separate readable-field
and writable-form subsections; the write subsection appears only when controlled
writes are enabled. **SQL read permissions** is a separate card containing its
own enable switch and shows its object scope only while reviewed SQL reads are
enabled. The form card's status badge exposes the effective write state without
requiring the user to infer it from disabled controls.

Selecting an `Allow every ...` option hides its redundant allowlist or mapping
editor. Clearing the option reveals the editor again with its unsaved selection
still intact. Once the broad mode is saved, the corresponding explicit
allowlist is normalized to empty as required by the configuration contract.

Inactive controls retain their loaded values unless the configuration contract
requires them to be empty. Enabling controlled writes always enables and locks
human approval because it is mandatory.

Form allowlists use searchable multi-select catalogs. After a form is selected,
the dashboard loads its field metadata on demand for readable, creatable, and
updatable field selection. The catalogs query metadata only: they never read
form records or write to Helix. Discovery uses the saved credential for the
selected environment and is intentionally independent of the current access
policy so that an administrator can add a form that is not permitted yet.

Catalog requests are bounded, searchable, cached according to the configured
metadata TTL, and protected by the same loopback and request-token controls as
configuration writes. The dashboard may start the local AR API bridge for
metadata discovery and keeps an owned bridge available until the dashboard
stops. Fields matching the configured sensitive-name protections cannot be
selected.

When SQL reads are enabled, the allowed-object editor provides the same
searchable selection flow using qualified `schema.object` names and object
types. Its fixed metadata query reads only the PostgreSQL catalogs and excludes
system schemas. BMC AR API SQL normally requires an administrative credential;
the dashboard therefore runs a bounded, one-row catalog query to verify that
capability for each saved environment credential. It does not read application
records. While verification is in progress, or after administrator access is
confirmed as unavailable, the SQL enable switch is disabled. A confirmed
non-administrator result is shown explicitly and prevents SQL reads from being
enabled in a save request.

Connectivity, bridge, and credential failures are reported as an unverified
state with a **Retry** action; they are not misreported as missing administrator
permission. Replacing a credential also keeps SQL unavailable until the new
credential is saved and checked. Form reads and controlled form writes remain
independent of this SQL capability check. Existing SQL allowlists remain
preserved when SQL is unavailable.

The dashboard does not expose manual text or JSON editors. Values already in the
configuration remain visible as selected items and are preserved even when the
environment no longer returns them. Advanced cases that cannot be represented
by the metadata selectors can be configured directly in `helix.yaml`. Saving
through the dashboard normalizes the YAML representation; comments in
`helix.yaml` are not retained.

## Sensitive-field protection

Sensitive-field protection is enforced before form and field allowlists. The
comparison is case-insensitive: `sensitive_fields` rejects exact field names,
while `sensitive_field_markers` rejects any field name containing a configured
marker. The default values for every environment are:

```yaml
sensitive_fields:
  - Password
  - Authentication String
  - Access Token
  - Refresh Token
  - API Key
  - Private Key
sensitive_field_markers:
  - password
  - passwd
  - token
  - secret
  - api key
  - private key
  - credential
  - authentication string
```

These controls deliberately do not appear in the dashboard because weakening
them can expose credentials or other secret material through otherwise broad
field permissions. The dashboard preserves each policy's loaded values when it
saves other settings. An administrator who has a justified exceptional case
can edit the corresponding policy directly in `helix.yaml`, then restart the
MCP client; removing a protection should be treated as a security-sensitive
configuration change.

The Client Gateway ports remain fixed at `46000`, `47000`, and `48000` for
DEV, QA, and PROD. The remote destinations reached by those listeners belong
to the authorized external connectivity layer and are outside this dashboard.

## Credential handling

Credentials are write-only in the browser:

- the state API returns only configured, source, and editable flags;
- usernames, passwords, authentication strings, and raw secret references are
  never returned;
- leaving replacement disabled preserves the existing credential;
- credentials supplied by the process environment cannot be replaced by the
  dashboard;
- credentials written to `.env` remain JSON values and the file is forced to
  private permissions on platforms that support POSIX modes.

The dashboard uses the existing per-environment credential contract. Treat
the browser session and local account as administrative access.

## Server updates

The **Server** card in the top summary shows the active release status and
latest stable version, matching the Helix MCP Knowledge dashboard. It checks
automatically when the page opens and also provides an explicit refresh action.
Checking never changes the installation. Installing is a separate, explicitly
confirmed action and is available only after setup has created the stable
launcher.

The update runs in a detached worker because the dashboard's own Python runtime
may be replaced. The page temporarily loses its loopback connection, polls for
the new dashboard process, and reloads state after it returns. The worker
verifies the GitHub release digest, installs an isolated runtime, backs up local
configuration and state, rebuilds the Java bridge, runs readiness checks, and
switches the stable launcher only after validation. Failed updates keep or
restore the previous active runtime. See [Installation](installation.md) for
the complete transaction and client restart behavior.

## Save transaction

Every state response includes a revision derived from both `helix.yaml` and
`.env`. A save is rejected if either file changed after the page loaded.

For an accepted save, the service:

1. validates the closed browser payload;
2. builds a complete candidate configuration in memory;
3. validates it through `SingleInstanceConfig`;
4. writes same-directory temporary files and replaces the targets atomically;
5. reloads both files through the normal runtime loaders;
6. restores the previous bytes if final validation fails.

No configuration is hot-reloaded. Restart the MCP client after a successful
save. This intentionally discards in-memory clients and caches and causes all
pending plans to be checked again against current policy before application.

## HTTP security boundary

The dashboard:

- binds only to a loopback host;
- validates the `Host` and browser `Origin` headers;
- requires a process-local random token for every POST request;
- bounds JSON request bodies to 64 KiB;
- serves restrictive CSP, no-store, no-referrer, and anti-framing headers;
- suppresses HTTP request logging;
- returns stable, sanitized errors without rejected values.

The read-only state endpoint contains no paths beyond file names and no secret
material. It exposes a non-secret installation fingerprint only to distinguish
the dashboard from another local installation during startup. The dashboard is
not designed for remote exposure. Do not publish the port through a reverse
proxy, tunnel, or shared network listener.
