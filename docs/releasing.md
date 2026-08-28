# Releasing

Versions follow Semantic Versioning and are distributed through GitHub
Releases. PyPI is not currently part of the release process.

## Preparation

1. Start from a clean `main` synchronized with `origin/main`.
2. Select the version and update `project.version` in `pyproject.toml`.
3. Move `Unreleased` entries into a dated changelog section.
4. Confirm that the tree contains no secrets, client data, private topology,
   organization-specific configuration, or proprietary binaries.
5. Run every check in `docs/development.md` and build the wheel and sdist.
6. Install the wheel and run `helix-mcp-setup --dry-run`.
7. Inspect the source archive and wheel independently of the working tree.

Release tests use only local doubles. They never perform live writes or access
PROD.

## Tagging

Publish the release commit before creating an annotated tag:

```text
git tag -a vX.Y.Z -m "Helix MCP Gateway X.Y.Z"
git push origin vX.Y.Z
```

`.github/workflows/release.yml` verifies the version, repeats all checks,
builds the wheel and sdist, generates `SHA256SUMS`, and creates the release.
Never move or reuse a published tag.

## Verification

A release is complete only when:

- the Release workflow succeeds;
- the release contains one wheel, one sdist, and `SHA256SUMS`;
- published hashes match downloaded artifacts;
- a clean wheel installation passes its smoke test;
- the tag points to the reviewed release commit;
- secret and confidentiality scans pass on both artifacts.

Optional acceptance against an authorized environment runs separately from
the published wheel. Its raw evidence remains private. If a release workflow
fails, fix the repository and publish a new Semantic Version rather than
replacing artifacts manually.
