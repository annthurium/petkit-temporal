# PetKit Temporal

FastAPI + Temporal backend and React frontend for controlling PetKit feeders.
See README.md for setup and running.

## Architecture decisions

Read `docs/adr/` before changing architecture. Its README has the index and
template. When you make a significant design decision, add a new ADR (don't
rewrite old ones; supersede them).

## Commands

- Tests: `uv run pytest`
- Frontend type-check: `cd frontend && npx tsc -b`
