# Controlled external evaluation guide

This guide is for qualified BMC Helix professionals evaluating Helix MCP
Gateway in an authorized local environment. It covers a clean installation,
one bounded read scenario, and one optional human-approved update scenario.

The objective is to evaluate whether a new user can install, understand, and
operate the gateway safely without assistance from the maintainer. It is not a
performance benchmark, a penetration test, or authorization to use customer or
production data.

## Participation boundaries

Before starting, confirm all of the following:

- you are authorized to install the local software and use the selected BMC
  Helix account;
- the first live test will use DEV or another non-production environment;
- the account, forms, fields, and records used in the evaluation are within
  your approved scope;
- any record used for a write test is fictional or synthetic and has no
  workflow, notification, integration, or business impact;
- you will not share credentials, endpoints, organization names, private form
  or field names, entry identifiers, SQL, values, rows, raw logs, configuration
  files, or unredacted screenshots;
- suspected vulnerabilities will be reported privately through the repository
  Security tab rather than a public issue.

Stop the evaluation if any boundary cannot be confirmed.

## What to record

Use a pseudonymous tester identifier and the
[evaluation feedback template](evaluation-feedback-template.md). Record only:

- gateway release;
- operating system and architecture;
- Python and Java versions;
- BMC Developer Studio / AR API release;
- MCP client name and version;
- elapsed time for installation and each scenario;
- ready/not-ready states, failed check names, and stable public error codes;
- usability observations written with fictional examples.

Keep private paths, target details, policy content, and raw diagnostics on the
tester's workstation. They are not required for the evaluation report.

## Stage 1: install and configure

1. Read the [compatibility matrix](compatibility.md) and confirm that the
   platform is tested or explicitly accept that it is unvalidated.
2. Download the latest stable wheel and `SHA256SUMS` from
   [GitHub Releases](https://github.com/hvolckaert/helix-mcp-gateway/releases).
3. Follow the verified [release installation](installation.md) procedure.
4. Run guided setup and open the local dashboard at
   `http://127.0.0.1:8766/`.
5. Configure only the authorized DEV credential required for the evaluation.
   QA and PROD may remain unconfigured; they will be unavailable without
   blocking DEV. Do not invent or copy credentials for unused environments.
6. Configure a narrow DEV readable-form and readable-field scope. Select only
   a fictional test form when possible. Keep QA and PROD read-only and outside
   the evaluation scenarios.
7. Save the configuration. Allow the dashboard to reload a managed OpenClaw
   connection, or reconnect a generic stdio client manually.
8. Run non-live preflight first, followed by the explicit DEV live check:

   ```text
   /path/to/helix-mcp-check --dotenv /path/to/.env
   /path/to/helix-mcp-check --dotenv /path/to/.env --live --environment dev
   ```

The non-live and live checks must report `ready` before proceeding. The live
check authenticates and disconnects without reading forms or records.

### Stage 1 success criteria

- the release checksum was verified;
- setup completed or identified a clear, actionable pending prerequisite;
- the dashboard remained bound to loopback;
- the saved policy matched the intended narrow DEV scope;
- non-live and DEV live preflight reported `ready`;
- the MCP client discovered all 19 tools;
- no secret or private business information appeared in public output.

If installation or preflight fails, stop and use the
[troubleshooting guide](troubleshooting.md). Do not work around a failed safety
check by weakening file permissions, exposing the dashboard remotely, or
copying proprietary BMC libraries into the repository.

## Stage 2: bounded read scenario

This scenario proves explicit targeting, connectivity, policy-filtered
metadata discovery, and a small form read. It does not use SQL and does not
modify Helix.

Privately choose one authorized fictional DEV form and two or three
non-sensitive fields. Then give the connected agent this prompt, replacing the
bracketed descriptions locally without copying the private names into the
evaluation report:

> Work only in DEV. List the configured targets and check DEV health. Find or
> verify the authorized fictional test form described as [private local form
> reference]. List only enough field metadata to identify the two or three
> approved non-sensitive fields described as [private local field references].
> Query at most three synthetic records and return only those approved fields.
> Do not use SQL, do not access QA or PROD, and do not plan or execute a write.
> Summarize which policy or permission boundary stopped any unavailable step.

### Stage 2 success criteria

- every live call named `dev` explicitly;
- `list_targets` and `health_check` returned sanitized results;
- form and field discovery exposed only policy-visible metadata;
- `query_form` returned no more than three records and only the requested
  non-sensitive fields;
- no SQL plan or write plan was created;
- the agent explained a denied step instead of attempting to bypass it.

Record the elapsed time, whether the instructions were understandable, and any
stable error code. Do not copy the returned rows into the feedback report.

## Stage 3: optional controlled update scenario

Skip this scenario when the tester lacks an authorized synthetic DEV record or
when the installation has not passed the bounded read scenario. SQL and create
operations are outside the baseline evaluation.

Before enabling write access in the dashboard:

1. select exactly one fictional DEV form;
2. select exactly one non-sensitive, updateable field;
3. confirm that changing the field cannot trigger notifications, integrations,
   escalations, reconciliation, or business workflow;
4. record the original fictional value privately so it can be restored;
5. keep QA and PROD outside the test's write scope;
6. save and reload or reconnect the MCP client as described in Stage 1.

Give the agent this prompt:

> Work only in DEV. Read the current value of the one approved non-sensitive
> field on the authorized synthetic record described by my private local
> reference. Prepare an update plan that changes only that field to the agreed
> fictional test value, with reason "Private preview controlled update test".
> Display the environment, form, entry identifier, current and proposed values,
> reason, plan ID, digest, status, and expiry. Do not apply the plan. Stop and
> wait for my explicit approval in a later message.

Review the complete plan. If it is correct, approve it in a separate message:

> I approve this exact pending DEV update plan. Retrieve the same plan, verify
> that its plan ID, digest, target, values, and pending status are unchanged,
> apply it exactly once, and read the approved field back. Do not create a
> replacement plan and do not modify any other record or environment.

After verifying the result, restore the original fictional value through a new
plan and a new explicit approval. Never retry an `outcome_unknown` write. Stop
and investigate it according to [form-writes.md](form-writes.md).

### Stage 3 success criteria

- planning did not modify Helix;
- the planning and approval messages were separate;
- execution used the exact reviewed plan ID and digest once;
- a bounded read confirmed the expected fictional value;
- restoration used a separate reviewed plan;
- no QA or PROD write was planned or executed;
- audit output remained sanitized.

## Submit feedback safely

Complete the [evaluation feedback template](evaluation-feedback-template.md)
without private installation details.

- Use a [bug report](https://github.com/hvolckaert/helix-mcp-gateway/issues/new?template=bug_report.yml)
  for a reproducible product defect.
- Use a [support request](https://github.com/hvolckaert/helix-mcp-gateway/issues/new?template=support_request.yml)
  when the intended workflow is unclear or blocked.
- Use a [feature request](https://github.com/hvolckaert/helix-mcp-gateway/issues/new?template=feature_request.yml)
  for a bounded improvement.
- Use [private vulnerability reporting](https://github.com/hvolckaert/helix-mcp-gateway/security/advisories/new)
  for a suspected security issue or any report that cannot be sanitized.

When uncertain, do not publish the detail. Report the general stage and stable
error code first, then agree on a safe private diagnostic process.

## End the evaluation

1. Cancel any pending SQL or write plans.
2. Restore any fictional value changed during the optional write scenario.
3. Revoke or rotate any dedicated temporary account in Helix. This is the
   authoritative way to terminate remote access.
4. If locally stored credentials must also be removed, stop the MCP client and
   dashboard, then clear the exact `HELIX_CREDENTIAL_DEV`,
   `HELIX_CREDENTIAL_QA`, and `HELIX_CREDENTIAL_PROD` values that were used from
   their approved secret source. The dashboard supports write-only replacement,
   not deletion.
   Never print or source the `.env` file while doing this. Preflight is expected
   to report no configured target for a live check after the last credential is
   removed.
5. Reconnect the MCP client so it no longer uses the previous in-memory
   configuration.
6. The current release has no automated uninstall command. Do not guess at
   cleanup paths or use recursive deletion against a home, workspace, or shared
   BMC installation directory. Remote access is already terminated by account
   revocation; request a platform-specific removal procedure through the
   support channel if complete local removal is required.
7. Retain only the sanitized feedback template. Delete local screenshots or
   diagnostics that contain private installation information according to the
   tester's organization policy.

The gateway never grants permissions that the selected Helix account does not
already have. Revoking or rotating that account remains the authoritative way
to terminate remote access.
