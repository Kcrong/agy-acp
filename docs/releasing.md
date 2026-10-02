# Releasing agy-acp

PyPI releases use GitHub Actions Trusted Publishing. No PyPI token, username, password, or repository secret belongs in the repository or workflow.

## One-time configuration

1. Verify the publishing PyPI account email and enable two-factor authentication.
2. Create a pending GitHub publisher on PyPI with these exact values:
   - PyPI project name: `agy-acp`
   - Owner: `Kcrong`
   - Repository name: `agy-acp`
   - Workflow name: `publish.yml`
   - Environment name: `pypi`
3. Create a GitHub Actions environment named `pypi` with all of these protection rules:
   - Under **Deployment branches and tags**, choose **Protected branches only**. The `main` protection ruleset must remain active.
   - Require `Kcrong` as the sole release reviewer.
   - Leave **Prevent self-review** disabled so the sole maintainer can approve a release they initiated.

Do not leave the PyPI publisher environment unrestricted as `(Any)`. The single-maintainer approval model intentionally treats the `Kcrong` repository administrator account as the publication trust boundary; compromise or misuse of that account can authorize publication.

A pending publisher does not reserve the project name. The first successful trusted publication creates the project if the name remains available.

## Prepare a release

1. Change only `[project].version` in `pyproject.toml` to the intended final or canonical PEP 440 prerelease version, such as `X.Y.Z` or `X.Y.ZaN`.
2. Run `uv lock` so the root package version in `uv.lock` matches.
3. Run the complete local validation documented in `CONTRIBUTING.md`.
4. Merge the reviewed change through a ready pull request after all four Linux/macOS and Python 3.13/3.14 checks pass.
5. Create and push an annotated `v<version>` tag that exactly matches the reviewed project version and points to its commit on `main`.

Do not create a Windows job or support claim. Do not reuse a version or distribution filename that has reached PyPI; deletion does not make it reusable.

## Publish

1. Manually run **Publish to PyPI** and enter the existing `v<version>` tag.
2. Wait for tag validation, the four-cell test matrix, the pinned experimental ACP TCK, one artifact build, Twine strict checking, and package smoke validation.
3. Review the exact commit, version, artifact SHA-256 values, and downloaded `pypi-distributions` artifact.
4. Approve the protected `pypi` environment only when those values are the intended immutable release.

The publish job downloads the already-validated artifact and authenticates to PyPI through OIDC. It does not rebuild after approval.

## Verify

After publication, install the released version in a clean local virtual environment and verify:

```bash
python -m pip install "agy-acp==<version>"
agy-acp --version
agy-acp --help
```

Confirm the PyPI project page shows the intended source, issue, security, license, Python, Linux, and macOS metadata. Record the workflow run, tag, commit, and artifact hashes in the release notes.
