# Claude Code skills: analysis and use cases

Skills live in `.claude/skills/` and are listed in `skills-lock.json`. Each one adds its description to every session, so we keep the set small and remove what we do not use.

## Installed in this batch

| Skill | Source | What it gives us | Use cases in DC Land Hunter | Caveats |
|---|---|---|---|---|
| `brainstorming` | obra/Superpowers | Structured design dialogue before building anything new | Shape a new feature before it goes into `docs/PLAN.md`, e.g. the weekly "Nowości" digest content or a second grid-proxy signal | Only the design step; plan goes to `docs/PLAN.md` (see rules below) |
| `writing-plans` | obra/Superpowers | Turns a spec into a step-by-step plan | Break a PLAN.md phase (e.g. Phase 4: kanban, notes, XLSX/PDF export, login UI) into tasks with file paths and tests | Default save path is `docs/superpowers/plans/`. Overridden in CLAUDE.md: write to `docs/PLAN.md` |
| `executing-plans` | obra/Superpowers | Works through a plan inline, one task at a time, with checkpoints | Run a single Phase 4 task (e.g. "Sprawdź działkę" minimal version) in the session where the owner is watching | Use when no subagents are wanted |
| `test-driven-development` | obra/Superpowers | Red, green, refactor for each behaviour | Scoring rules (60 MW gate, 10 ha gate, 6 MW/ha estimate, confidence labels); grid parsers against saved fixtures (PSE XLSX/PDF); ULDK response handling | Parsers with real PSE files come later (Phase 3); fixtures first |
| `systematic-debugging` | obra/Superpowers | Root-cause investigation before fixes | OSM voltage gaps, GPZ name matching (rapidfuzz scores), memory spikes on the 1.9 GB VPS (risk R7) | Helps only if we ask for root cause, not a quick patch |
| `verification-before-completion` | obra/Superpowers | Run the checks and show the output before claiming success | Before every PR: `ruff`, `mypy`, `pytest`, `pnpm lint/typecheck/test`; before updating STATUS.md demo steps | Pairs with the "fast checks" list in CLAUDE.md |
| `requesting-code-review` | obra/Superpowers | Review checklist before asking for a merge | Pre-PR self-review for scoring changes (weights in `config/scoring.yaml`), grid-confidence labelling, RODO handling of notes | Does not replace the owner's review |
| `receiving-code-review` | obra/Superpowers | Check review feedback technically before applying it | PR feedback from the owner or a reviewer, especially on scoring weights where a wrong change silently moves rankings | Use it to verify suggestions, not to agree with all of them |
| `finishing-a-development-branch` | obra/Superpowers | Chooses how to integrate finished work | End of a feature branch: push and open the PR | Its "merge locally" option conflicts with our rule (owner merges). Overridden in CLAUDE.md |
| `using-git-worktrees` | obra/Superpowers | Isolated worktree for feature work | Work on pipeline and web changes side by side without touching `main` or the VPS checkout (risk R17) | Keep worktrees out of git (`.worktrees/` must be ignored) |
| `karpathy-guidelines` | multica-ai/andrej-karpathy-skills | Short rules: state assumptions, minimal code, surgical diffs, define success criteria | Keeps PRs small and reviewable: no speculative config knobs in the scorer, no drive-by refactors in parsers | Overlaps heavily with `rigorous-coding`, which we installed earlier. See open decision below |
| `agent-browser` | vercel-labs/agent-browser | Browser automation CLI with accessibility-tree snapshots | Manual smoke checks of the map and card views on a phone-size viewport; checking Polish text on rendered pages | The skill file is a stub. Real instructions come from the CLI (`npm i -g agent-browser && agent-browser install`), which is not installed here. Needs owner approval before installing on the VPS |
| `webapp-testing` | anthropics/skills | Playwright-based testing of local web apps | Phase 4 acceptance E2E: login, filter and open a card, check a parcel, move a deal, export, watch → Nowości | Uses Playwright. Preferred over agent-browser for repeatable E2E tests |
| `ui-ux-pro-max` | nextlevelbuilder/ui-ux-pro-max-skill | Searchable UI/UX rules (accessibility, touch targets, contrast, typography, charts) with a Python search script | Review of site cards (grid confidence badges), kanban on touch screens, chart legends for powiat-level data; checks for the 16 px gutter at phone width | Needs `python3`. The installed scripts make no network calls (checked). The image downloader lives in the uninstalled `design-system` skill, so keep that one out. Stack is detected from `web/package.json` |
| `frontend-design` | anthropics/skills | Avoids generic-looking UI; gives aesthetic direction | Dashboard and one-pager look: a clear, serious style for an investor team | Keep the palette consistent with `web/` tokens; all UI text stays in Polish |
| `pdf` | anthropics/skills | Read, extract and check PDFs with `pypdf` / `pdfplumber` | Extract tables from PSE PDFs (`pse_odb`, `pse_avail`, Phase 3); check the generated one-pager PDF | Generation of the PDF one-pager stays in code (WeasyPrint, Phase 4); this skill is for reading and checking |
| `xlsx` | anthropics/skills | Spreadsheet creation and editing with formula and format rules | XLSX export of the shortlist (Phase 4); reading the manual CSV/XLSX imports (GPZ mapping, offer CSV) | Repo already uses `openpyxl`. Keep export column names in Polish |
| `doc-coauthoring` | anthropics/skills | Structured workflow for shared docs | Drafting RUNBOOK sections, the partner one-pager copy, and the owner's decision notes | Use the repo's English for docs; the one-pager copy itself is Polish |

## Not installed (and why)

| Skill | Reason |
|---|---|
| `using-superpowers`, `diagnosing-superpowers` | `using-superpowers` is the bootstrap for the whole Superpowers plugin and expects its SessionStart hook. We did not install the hook, so it would only add noise |
| `subagent-driven-development`, `dispatching-parallel-agents` | Revisit once there is a real parallel workload. Overlaps with the Agent tool |
| `writing-skills` | Overlaps with `skill-creator` (in `anthropics/skills`), which we can install later |
| `skill-creator`, `mcp-builder`, `claude-api` | Not needed yet. `claude-api` becomes relevant only if we add LLM features to the app |
| `docx`, `pptx`, `theme-factory`, `slack-gif-creator`, `algorithmic-art`, `canvas-design`, `brand-guidelines`, `internal-comms`, `web-artifacts-builder`, `academy-guide`, `discernment-nudge`, `slides`, `banner-design`, `brand`, `design`, `design-system` | No match with the product. The one-pager is PDF |
| `ui-styling` | Possible later: the web app uses shadcn-style components (`web/components.json`). Add it when we build new components |

## Rules added to CLAUDE.md in this batch

- Plans go in `docs/PLAN.md`, not `docs/superpowers/plans/`. This overrides `writing-plans`.
- Never merge a branch locally or into `main`. Open a PR and let the owner merge. This overrides the "merge locally" option in `finishing-a-development-branch`.

## Open decisions for the owner

1. **Duplicate guidance.** `rigorous-coding` and `karpathy-guidelines` say much the same thing. Keep one? Karpathy's version is more detailed (surgical changes, success criteria). `rigorous-coding` is shorter.
2. **agent-browser CLI.** Install the CLI on the dev machine only, or skip it and use `webapp-testing` (Playwright) for everything?
3. **Attribution.** Commit messages and PR bodies must not name the model (system rule). The skill batch commit therefore has no `Co-Authored-By` line naming a model. Confirm this is what you want.
