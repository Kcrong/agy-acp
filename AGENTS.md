# agy-acp Development Guidelines

This document applies to all implementation and modification work within the `agy-acp` repository.

## 1. Branching and Code Review

- Do not push directly to the default branch (e.g., `main`, `master`).
- Always create a feature branch based on the latest default branch before starting any modification work.
- Explicitly specify the target feature branch when pushing.
- All changes must be merged only after creating a PR and undergoing a code review.
- Do not merge until issues identified during the review are addressed and necessary verifications are completed.

## 2. Local Work-Tracking Documents

- Do not commit `north_star.md`, `roadmap.md`, or `tasks.md`.
- Keep any document used only for transient planning, progress tracking, checklists, or agent working state local and untracked, regardless of its filename.
- If such a document is already tracked, remove it from Git tracking without deleting the local working copy.
- Commit durable product requirements, architecture decisions, and user-facing documentation only when they are intended repository artifacts rather than transient work trackers.

## 3. Dependency and Action Versions

- Prefer the latest stable releases of dependencies and GitHub Actions when they are compatible, to minimize exposure to known vulnerabilities.
- Pin npm dependencies to exact versions and GitHub Actions to full commit SHAs with the corresponding release tag in a comment.
- Before adopting a new major release, review its release age, changelog, advisories, peer/runtime compatibility, and full test results.
- If the newest major is recently released, unstable, or not yet supported by the surrounding ecosystem, use the latest stable release from the previous compatible major and document the reason.
- Do not use prerelease versions by default.

## 4. PR Title and Body

- Write the PR title concisely in English.
- Write the PR body in English.
- Keep it concise by focusing only on the key details, adding background explanations only when necessary to understand the changes.
- Use the following sections in order:

```markdown
## What for

## What changed

## Why

## How tested
```

* In the `How tested` section, record the actual tests executed, their results, and artefacts such as screenshots if it possible.

## 5. Functional Testing

* Prioritize verifying the reliability of the feature.
* Utilize end-to-end tests as much as possible to verify actual user flows.
* Verify not only the normal flow but also major failure, cancellation, and boundary conditions to the extent possible.
* Use `screen` or `tmux` as needed when verifying long-running or interactive processes.
* Use unit and integration tests to supplement end-to-end tests.
* Record the executed test commands and results in the PR body.

## 6. Git commit message

* Use the Conventional Commits format for all commit messages.

```text
<type>(<scope>): <description>
```

* Standard types are `feat`, `fix`, `test`, `refactor`, `docs`, `chore`, `ci`, `build`, and `perf`.
* Contain only one logical change per commit.
* Example: `feat(acp): add agy process bridge`
