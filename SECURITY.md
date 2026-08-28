# Security policy

## Supported versions

| Version | Security support |
| --- | --- |
| 0.6.x | Yes |
| 0.5.x and earlier | No |

Security fixes target the latest supported release line.

## Responsible disclosure

Do not publish vulnerabilities, credentials, Helix data, private topology, or
exploitation details in a public issue.

Use **Report a vulnerability** in the repository's Security tab. If private
reporting is unavailable, open an issue without sensitive details and ask the
maintainer for a private channel.

When possible, include:

- affected version and commit;
- affected component and a local reproduction environment;
- expected impact;
- minimal steps using fictional data;
- any known mitigation.

Do not test a vulnerability against PROD, a third-party service, or a Helix
installation without explicit authorization.

## Scope

This policy covers the Python gateway, original Java bridge source, templates,
and procedures in this repository. BMC products, Kaazing, Java, and their
libraries retain their own support channels and security policies.
