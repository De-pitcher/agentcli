# agentcli Development Workspace

Identity: ICM workspace for the agentcli 7-phase development roadmap.
Form: Umbrella (one pipeline per phase, shared reference layer).
Repo: C:\Users\sam\Documents\sparkz\agentcli (git@github-second:De-pitcher/agentcli.git)

## Where to go for…

| Question | Go to |
|---|---|
| Current phase status + acceptance criteria | `02_phase4-agent-loop/CONTEXT.md` |
| Architecture decisions (locked) | `_shared/architecture.md` |
| Quality gates (must pass before any PR) | `_shared/quality-gates.md` |
| Git/PR/SSH workflow | `_shared/git-workflow.md` |
| Full 7-phase roadmap + current pointer | `_shared/phase-roadmap.md` |
| What Phase 1 built and decided | `_archive/phase1-foundation/CONTEXT.md` |
| What Phase 2 built and decided | `_archive/phase2-routing/CONTEXT.md` |
| What Phase 3 built and decided | `_archive/phase3-sub-agents/CONTEXT.md` |
| Phase 5 through 7 specs (pre-staged) | `03_phase5-memory-context/` … `05_phase7-ecosystem-release/` |

## Rules for this workspace

- **Do NOT modify Python source under `agentcli/`** from here — use the repo.
- **Do NOT duplicate content** — link to the phase CONTEXT.md, never copy it.
- **Do NOT let a CONTEXT.md grow beyond one screen** — move detail to `references/`.
- **Status is derivable** — "what phase is active" = the lowest-numbered non-archived folder.
- **Archive a phase** after its final PR merges — move `01_phase3…` into `_archive/phase3-…/`.
- This workspace inherits all rules from `C:\Users\sam\Documents\sparkz\AGENTS.md`.
