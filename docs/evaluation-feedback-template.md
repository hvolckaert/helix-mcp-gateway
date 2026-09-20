# External evaluation feedback template

Copy this template into a private response or a sanitized GitHub issue. Do not
commit completed reports containing personal information or private Helix
details to the repository.

## Evaluation context

- Tester ID: `T-___` (pseudonym only)
- Date:
- Gateway release:
- Platform and architecture:
- Python version:
- Java version:
- BMC Developer Studio / AR API release:
- MCP client and version:
- Installation type: OpenClaw-managed / standalone stdio

Do not include usernames, organizations, endpoints, ports, private paths,
credentials, form or field names, entry identifiers, SQL, values, rows, raw
logs, configuration files, or unredacted screenshots.

## Stage results

| Stage | Result | Elapsed time | Safe observation |
| --- | --- | ---: | --- |
| Release verification and installation | Pass / Blocked / Skipped | | |
| Dashboard configuration | Pass / Blocked / Skipped | | |
| Non-live preflight | Pass / Blocked / Skipped | | |
| DEV live preflight | Pass / Blocked / Skipped | | |
| MCP initialization and 19-tool discovery | Pass / Blocked / Skipped | | |
| Bounded read scenario | Pass / Blocked / Skipped | | |
| Optional controlled update | Pass / Blocked / Skipped | | |
| Credential removal or revocation | Pass / Blocked / Skipped | | |

## Installation and first-use experience

Rate each item from 1 (very poor) to 5 (excellent).

| Question | Score | Safe comment |
| --- | ---: | --- |
| Prerequisites were understandable | | |
| Release download and verification were clear | | |
| Guided setup was understandable | | |
| Dashboard terminology was clear | | |
| Policy configuration was understandable | | |
| Errors suggested an actionable next step | | |
| MCP client integration was clear | | |
| Approval boundaries were understandable | | |
| Overall confidence in the safety model | | |

## Problems found

Repeat this block for each problem.

- Stage:
- Expected behavior:
- Observed behavior, using fictional examples:
- Reproducible with fictional data: Yes / No / Unknown
- Stable public error code, if any:
- Failed check name, if any:
- Frequency: Once / Intermittent / Always
- Workaround used: None, or a safe high-level description
- Severity:
  - Blocker: evaluation cannot continue safely;
  - Major: core scenario fails, but installation remains safe;
  - Minor: confusing or inconvenient behavior;
  - Suggestion: improvement rather than a defect.

Do not attach raw logs or configuration. A suspected security problem belongs
in a private security report, not this section.

## Open feedback

- What was the hardest part to understand?
- Which step required maintainer assistance?
- What made you trust or distrust the workflow?
- Which documentation was missing or difficult to find?
- What is the single most valuable improvement before wider release?
- Would you use the gateway again in a controlled environment? Why or why not?
- May an anonymized quotation be used publicly? Yes / No

## Privacy confirmation

- [ ] I removed credentials, endpoints, organization and customer names.
- [ ] I removed private paths, form and field names, identifiers, SQL, values,
      rows, logs, configuration, and unredacted screenshots.
- [ ] This report does not describe a suspected vulnerability.
- [ ] Any public example is fictional and cannot be traced to a live system.
