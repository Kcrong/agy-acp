# Releasing agy-acp

PyPI releases use GitHub Actions Trusted Publishing. No PyPI token, username, password, or repository secret belongs in the repository or workflow.

## One-time configuration

1. Verify the publishing PyPI account email and enable two-factor authentication.
2. On the existing `agy-acp` PyPI project, add or verify a GitHub Trusted Publisher with these exact values:
   - PyPI project name: `agy-acp`
   - Owner: `Kcrong`
   - Repository name: `agy-acp`
   - Workflow name: `publish.yml`
   - Environment name: `pypi`
3. Create a GitHub Actions environment named `pypi` with all of these protection rules:
   - Under **Deployment branches and tags**, choose **Protected branches only**. The `main` protection ruleset must remain active.
   - Require `Kcrong` as the sole release reviewer.
   - Leave **Prevent self-review** disabled so the sole maintainer can approve a release they initiated.
4. Create an active repository tag ruleset targeting `refs/tags/v*` with tag updates and deletions restricted and no bypass actors. The release workflow must be allowed to create a new tag, but an existing release tag must remain immutable.
5. Keep squash merging as the only merge method and keep **Default to pull request title for squash merge commits** enabled. This makes the validated PR title the Conventional Commits input on `main`.
6. Require the four Linux/macOS and Python 3.13/3.14 CI checks and the Conventional PR title check before merge.

Do not leave the PyPI publisher environment unrestricted as `(Any)`. The single-maintainer approval model intentionally treats the `Kcrong` repository administrator account as the publication trust boundary; compromise or misuse of that account can authorize publication.

## Version policy

Every PR title must follow Conventional Commits as documented in `CONTRIBUTING.md`. A squash merge uses that title and the appended PR number as the `main` commit subject, and the tagging Action analyzes every commit since the previous stable tag:

- `feat` produces a minor bump.
- Any allowed type with `!` produces a major bump.
- `build`, `chore`, `ci`, `docs`, `fix`, `perf`, `refactor`, `revert`, `style`, and `test` produce a patch bump.

Package metadata is derived from the immutable `v<version>` tag through Hatch VCS. There is no static version field to update and no separate release PR. Do not manually create, move, or delete release tags, and do not reuse a version or distribution filename that has reached PyPI.

## Automatic release flow

1. Merge a ready change only after its PR title and all four supported CI cells pass.
2. The resulting push runs the same complete test, lint, type-check, ACP TCK, build, archive, and Git-install gates on `main`.
3. A successful `main` CI run starts `publish.yml`; pull request CI runs and failed or cancelled `main` runs cannot enter the release jobs.
4. The workflow confirms the tested commit belongs to `main` and compares it with the latest release tag. A commit already contained in a newer tag exits without back-tagging; otherwise the latest tag must be its ancestor.
5. For every commit after that preceding tag, the workflow queries the associated merged PR and requires the commit subject to equal the validated PR title with its `(#number)` suffix.
6. The SHA-pinned Conventional Commit tagging Action calculates the highest required SemVer bump across those commits and creates an annotated `v<version>` tag on the validated commit.
7. The workflow checks out that exact tag, verifies the tag object and commit identity, builds once, runs Twine and package smoke validation, records SHA-256 hashes, and uploads the immutable `pypi-distributions` artifact.
8. Before requesting environment approval, the workflow queries PyPI for the release and compares every published filename and SHA-256 with the validated distributions. A complete matching release exits successfully, a partial matching release stages only the missing files as an immutable workflow artifact, and any hash mismatch or unexpected file stops the release.
9. When files remain, the publish job enters the protected `pypi` environment. Review the tag, commit, version, artifact hashes, and CI result, then approve the deployment.
10. The privileged job runs only the SHA-pinned artifact download and PyPI Trusted Publishing Actions. It uploads the filtered validated files without rebuilding them or executing repository code, and duplicate tolerance remains disabled so a race or conflicting filename fails closed.

The workflow allows one active release at a time and uses `queue: max` to retain up to 100 waiting runs instead of replacing an older pending run. A retry recomputes the expected version from the preceding tag before it may reuse an existing tag, and verifies PyPI state by filename and SHA-256 before any upload. This makes fully published retries no-ops and allows a matching partial upload to resume safely. If PyPI changes after preflight, publication fails closed and the next serialized retry recalculates the missing files. Main CI concurrency is isolated by commit SHA, so rerunning historical CI cannot cancel validation for a newer commit. If `main` advances while a tested commit is releasing, that release remains valid; the later successful run starts from its tag and releases the subsequent commits. If the later commit is tagged first, ancestry detection makes the older run exit without creating a backward tag.

Do not create a Windows job or support claim. Standard GitHub-hosted Ubuntu and macOS runners remain the only release validation platforms.

## Verify

After publication, install the released version in a clean local virtual environment and verify:

```bash
python -m pip install "agy-acp==<version>"
agy-acp --version
agy-acp --help
```

Confirm the PyPI project page shows the intended source, issue, security, license, Python, Linux, and macOS metadata. Record the workflow run, tag, commit, and artifact hashes in the release notes.
