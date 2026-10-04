# Contributing

## Development setup

Use Linux or macOS with Python 3.13 or 3.14 and uv 0.12.21. Install the locked project environment without global packages:

```bash
uv sync --frozen --all-groups --python 3.14
```

## Validation

Run the core local gates with Python 3.14:

```bash
uv lock --check
uv run --frozen ruff format --check .
uv run --frozen ruff check .
uv run --frozen mypy
uv run --frozen pytest
mkdir -p build/reports
uv run --frozen --group tck python scripts/tck_smoke.py \
  --report-json "$PWD/build/reports/acp-tck-report.json"
uv build --no-build-isolation
uv run --frozen twine check --strict dist/*.whl dist/*.tar.gz
uv run --frozen python scripts/package_smoke.py "$PWD/dist"
```

Repeat the project test suite on Python 3.13. CI runs every gate on the supported Linux/macOS and Python matrix for ready pull requests and every push to `main`. Draft pull request revisions do not allocate test jobs. A successful `main` CI run starts the automatic tag and PyPI release workflow.

Add focused unit and end-to-end coverage for success, failure, cancellation, cleanup, and boundary behavior. Linux and macOS support must remain equivalent; do not add Windows support claims without a reviewed platform decision.

## Changes and review

Create a focused feature branch from current `main`, use Conventional Commits for commits, and open the pull request as a draft while revisions are still being pushed. Mark it ready for review only when the full CI matrix should run. Do not push directly to `main`. Address review findings and pass all supported CI cells before merge.

Every pull request title must follow Conventional Commits because squash merges use it, with the PR number appended, as the `main` commit subject and automatic versioning input:

```text
<type>(<optional-scope>)!: <description>
```

Use `feat` for a minor release, append `!` for a major release, and use `build`, `chore`, `ci`, `docs`, `fix`, `perf`, `refactor`, `revert`, `style`, or `test` for a patch release. Examples include `feat(acp): add streaming updates`, `fix(process): close inherited descriptors`, and `refactor!: remove the legacy transport`.

Use concise English `What for`, `What changed`, `Why`, and `How tested` sections in the pull request body. Never commit secrets, credentials, private endpoints, personal data, raw prompts or responses, conversation identifiers, environment dumps, machine-specific configuration, or transient planning files. Follow `SECURITY.md` for vulnerability reports.

Maintainers must follow `docs/releasing.md`. A successful `main` build calculates the next version from Conventional Commit titles, creates an immutable annotated tag, builds that exact tag, and advances the validated distributions to PyPI Trusted Publishing. The protected `pypi` environment retains the final publication approval gate.
