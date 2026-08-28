# Git & PR Workflow

## Identity

```
Git identity (repo-local):  De-pitcher <emmanwa000@gmail.com>  (three zeros — not two)
GitHub account:             De-pitcher
Remote alias:               github-second  (configured in ~/.ssh/config)
Remote URL:                 git@github-second:De-pitcher/agentcli.git
gh CLI path:                "C:\Program Files\GitHub CLI\gh.exe"  (call with & in PowerShell)
```

## Branch naming

| Type | Pattern | Example |
|---|---|---|
| New feature / phase work | `feat/<topic>` | `feat/phase3-sub-agents` |
| Bug fix | `fix/<topic>` | `fix/registry-cooldown` |
| Tooling, CI, docs, deps | `chore/<topic>` | `chore/post-audit-cleanup` |

## PR workflow

1. Branch from up-to-date `main`
2. Commit focused changes; keep commits scoped
3. Push branch: `git push -u origin <branch>`
4. Open PR via gh CLI:
   ```powershell
   & "C:\Program Files\GitHub CLI\gh.exe" pr create --title "..." --body "..." --base main
   ```
5. Wait for all 8 CI checks to go green (exact-match context names required)
6. Squash-merge — never merge commits, never rebase+force-push to main
7. Delete the branch after merge

## Main branch protection

- Direct pushes rejected — always use a PR
- 8 CI checks required (ubuntu+windows × py3.11-3.14)
- `enforce_admins: true` — owner cannot bypass
- Linear history enforced

## CHANGELOG convention

- All unreleased work goes under `## [Unreleased]` at the top of `CHANGELOG.md`
- Entries use `### Added`, `### Fixed`, `### Changed` headings
- When a version is released, `[Unreleased]` becomes `[x.y.z] — YYYY-MM-DD`

## Common mistakes to avoid

- Never use the two-zero email `emmanwa00@gmail.com` — it is always three zeros `emmanwa000@gmail.com`
- Never `git push origin main` directly — it will be rejected
- Never call `gh pr create` without the `&` prefix in PowerShell
- Never pass glob patterns unquoted in PowerShell npm scripts
