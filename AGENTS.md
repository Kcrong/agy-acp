# agy-acp Development Guidelines

This document applies to all implementation and modification work within the `agy-acp` repository.

## 1. Branching and Code Review

- Do not push directly to the default branch (for example, `main` or `master`).
- Create a feature branch from the latest default branch before modification work.
- Explicitly name the feature branch when pushing.
- Merge changes only through a pull request after review and required verification.
- Keep a pull request in draft while pushing work-in-progress revisions; mark it ready for review only when the full CI matrix should run.
- A new revision on an already-ready pull request reruns the required checks; return it to draft before iterative pushes that should not allocate runners.
- Address review findings and complete the relevant checks before merging.

## 2. Maintainer Ownership and Explanations

- Write all pull request content and code comments in English.
- Write repository-facing explanations from the project's maintainer perspective.
- Explain the technical need, intended outcome, implementation, and relevant trade-offs in standalone terms.
- Describe what changed and why it belongs in the project.
- Never attribute a change to a user, requester, another maintainer, or external instruction; state the project rationale directly.
- Apply this framing to pull requests, commits, documentation, code comments, issue updates, and other durable repository content.
- Keep explanations accurate to the implemented behavior; do not invent rationale or hide unresolved risks.

## 3. Local Work-Tracking Documents

- Do not commit `north_star.md`, `roadmap.md`, or `tasks.md`.
- Keep documents used only for transient planning, progress tracking, checklists, or agent working state local and untracked, regardless of filename.
- If such a document is already tracked, remove it from Git tracking without deleting the local working copy.
- Commit durable product requirements, architecture decisions, and user-facing documentation only when they are intended repository artifacts rather than transient trackers.

## 4. Implementation and CI Platform

- Use Python for the primary implementation.
- Support Ubuntu/Linux and macOS.
- Do not claim Windows support or add Windows GitHub Actions jobs unless the platform requirements are intentionally revised through a reviewed change.
- Support the two newest stable Python 3 feature releases (`3.N` and `3.(N-1)`) on every supported operating system.
- Run GitHub Actions only on standard GitHub-hosted Ubuntu and macOS runners.
- Run the test workflow only for pull requests that are ready for review; draft pushes and `main` pushes must not allocate test jobs.
- Protect `main` so its current pull-request head must pass all four Ubuntu/macOS and Python 3.13/3.14 checks before merge.
- Standard runners are free for public repositories, but workflows must remain within GitHub's job-duration, concurrency, and storage limits.
- Do not use larger runners unless the platform requirements are intentionally revised through a reviewed change.
- Keep dependency installation project-local. Do not require global installation or `sudo` for development and verification.

## 5. Dependency and Action Versions

- Prefer the latest compatible stable releases of dependencies and GitHub Actions to minimize exposure to known vulnerabilities.
- Pin dependencies reproducibly in the project lockfile and pin GitHub Actions to full commit SHAs with the corresponding release tag in a comment.
- Before adopting a new major release, review its release age, changelog, advisories, runtime compatibility, ecosystem support, and full test results.
- If the newest major is recent, unstable, or unsupported by the surrounding ecosystem, use the latest stable release from the previous compatible major and document the reason.
- Do not use prerelease versions by default.

## 6. Security and Public Repository Safety

- Do not commit secrets, credentials, tokens, private endpoints, personal data, or machine-specific configuration.
- Use documented placeholders and environment-based configuration for sensitive values.
- Treat generated artefacts, logs, fixtures, and test snapshots as publishable content before adding them to Git.

## 7. Pull Request Title and Body

- Write concise pull request titles and bodies in English.
- Focus on the change and its purpose, adding background only when needed to understand the decision.
- Use these sections in order:

```markdown
## What for

## What changed

## Why

## How tested
```

- In `How tested`, record the checks actually executed and their results, including relevant artefacts when available.

## 8. Functional Testing

- Prioritize feature reliability and real user flows.
- Use end-to-end tests where practical, including major failure, cancellation, and boundary conditions.
- Supplement end-to-end coverage with focused unit and integration tests.
- Use `screen` or `tmux` when needed for long-running or interactive verification.
- Record the exact executed test commands and results in the pull request.

## 9. Git Commit Messages

- Use Conventional Commits for every commit:

```text
<type>(<scope>): <description>
```

- Standard types include `feat`, `fix`, `test`, `refactor`, `docs`, `chore`, `ci`, `build`, and `perf`.
- Keep each commit to one logical change.
- Example: `feat(acp): add agy process bridge`
