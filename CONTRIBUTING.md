# Contributing

Thank you for helping improve `agy-acp`.

## Development setup

Requirements: Node.js `>=22.13.0`, npm 11, and an optional authenticated `agy >=1.2.14` for the real smoke test.

```bash
npm ci --ignore-scripts
npm run check
```

Dependencies must remain workspace-local and exactly pinned. Do not use `sudo` or global package installation for repository development.

## Workflow

1. Start from the latest `main`.
2. Create a focused feature branch. Never push changes directly to the default branch.
3. Keep each commit to one logical change and use Conventional Commits:

   ```text
   <type>(<scope>): <description>
   ```

4. Add unit tests and fake-process E2E coverage for behavioral changes.
5. Run `npm run check` locally.
6. Open a PR with an English Conventional Commit title and a concise Korean body using these sections:

   ```markdown
   ## 무엇을 위해

   ## 어떤 변경을

   ## 왜

   ## 어떻게 테스트했는지
   ```

7. Address review findings and re-run relevant tests before merge.

## Testing expectations

Reliability takes priority. Cover normal behavior plus malformed output, early exit, timeout, cancellation, concurrency, and cleanup when relevant.

The real authenticated test is optional for ordinary contributors and must never expose local credentials or model output:

```bash
RUN_REAL_AGY_E2E=1 \
AGY_E2E_AGY_PATH=/absolute/path/to/agy \
npm run test:e2e -- tests/e2e/real-agy.e2e.test.ts
```

## Security and privacy

Never commit credentials, tokens, conversation IDs, real prompt output, user paths, `.env` files, or Antigravity authentication state. Use deterministic fake fixtures. Report vulnerabilities through the process in [SECURITY.md](SECURITY.md), not a public issue.
