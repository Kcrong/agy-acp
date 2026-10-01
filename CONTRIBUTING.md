# Contributing

## Development setup

Use Linux or macOS with Python 3.13 or 3.14 and uv 0.12.21. Install the locked project environment without global packages:

```bash
uv sync --frozen --all-groups --python 3.14
```

## Validation

Run the same local gates required by CI:

```bash
uv run --frozen ruff format --check .
uv run --frozen ruff check .
uv run --frozen mypy
uv run --frozen pytest
uv build --no-build-isolation
```

Add focused unit and end-to-end coverage for success, failure, cancellation, cleanup, and boundary behavior. Linux and macOS support must remain equivalent; do not add Windows support claims without a reviewed platform decision.

## Changes and review

Create a focused feature branch from current `main`, use Conventional Commits, and open a concise English pull request with `What for`, `What changed`, `Why`, and `How tested` sections. Do not push directly to `main`. Address review findings and pass all supported CI cells before merge.

Never commit secrets, credentials, private endpoints, personal data, raw prompts or responses, conversation identifiers, environment dumps, machine-specific configuration, or transient planning files. Follow `SECURITY.md` for vulnerability reports.
