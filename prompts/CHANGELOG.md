# Prompt Changelog

The master prompt is a code artifact. Every change goes through this changelog (semver) and a validation run before it can be pinned by Arena Agent.

## v1.3.0 — 2026-11-12

- Added UN_SHRINK vertex group logic for loose hems
- Shrinkwrap offset increased 0.003 → 0.004 (fixed kneepad poke-through)
- Weight limit reduced 4 → 4 (unchanged; tested, mobile ceiling)

## v1.2.1 — 2026-11-08

- Data Transfer mapping: NEAREST_VERTEX → POLYINTERP_NEAREST (fixed elbow seams on jackets)

## v1.2.0 — 2026-11-05

- Initial Unity export flags locked
