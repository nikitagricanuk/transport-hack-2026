# Transport Hack backlog implementation handoff — 10 November 2026

## State and scope

Branch: `feat/transport-backlog`, based on `origin/main` at `6ca3e6f` (merged PR #20).
The user requested one PR for the implemented work. This is a **partial implementation for review**, not a claim that every acceptance criterion in the uploaded 14-PR plan is complete. Do not merge automatically.

PR #20 already supplied restriction-member repairs and fix reasons (B1/B2). This branch extends batch reason handling and the manual-edit fallback rather than duplicating those repairs.

The original safety constraints remain: no automatic fixes on import, no fix-all action, analysis is read-only, suggested fixes go through preview and explicit Apply, and no organizer-check endpoint is used during development. No Notion changes, organizer submissions, production deployments, or team communications were performed.

## Implemented

- **Routing/accessibility (B4–B8, substantial MVP):** parallel-way-aware car/walk graphs; virtual edge snapping; node-via turn restrictions and skipped-restriction reporting; route geometry/statistics; no-route diagnostics; original-document comparison; route probes inside reversible fix previews; LRU graph cache. Read-only route, isochrone, two-set stop catchment, PT parameter and PT isochrone endpoints live under `/api/documents/{id}/analysis/`.
- **Partial-edge reach:** time/distance cutoffs include fractions of edges; opposite directions are unioned before length calculation. Buffer/hull polygons, boundary warnings, and building coverage counts are exposed. Walking filters exclude explicit bans and separately mapped sidewalks. Pedestrian checks P1–P4 are info-only and do not change critical/warning totals.
- **PT MVP:** route pieces are separated at breaks; frequency-based boarding waits, riding, walking and bounded transfers; without/with-transfer bands; request-only headway overrides; sample walk/wait/ride explanations. JSON headways are explicitly `mock` when OSM intervals are absent. Optional XLSX/shapefile importer is dev-only; source spreadsheets/shapefiles are not included.
- **Analysis UI:** route/isochrone/catchment/PT tabs, map point picking, selected-object origins, original-file comparison, overlays, reviewed analysis fixes and route-probe output. Catchment stop sets can be selected in the list.
- **Editor (N1–N12, with limits below):** stop presets clear conflicting classification tags and warn about known route membership; signal-direction field removed; independent road/error visibility; renamed pedestrian layers; nodes and tram-signal visibility; blue square stops with labels; panel close controls using the existing Window toggles; Reports and Help menus; horse/wheelchair permissions remain available on paths; Ctrl/Cmd multiselect, Shift selection rectangle, grouped tag/template/delete actions and shared-vertex-safe group movement. Alt-click twice on a way selects a segment; `edit_segment` materializes endpoints, splits with relation rewriting, applies tags to the middle, and is previewed/undone as one transaction. Placement tools remain active after placement.
- **Signals (N13/N14):** green durations in seconds, computed cycle, yellow/all-red display, z/LOS in approach table, namespaced additional-arrow tag, tram signal placement/classification, and export/import preservation.
- **Closures (N15):** up to three diverse bounded alternatives using restriction-aware state paths; user selection changes overlay and load assignment. Missing-detour cards expose routing diagnostics/fixes where available.
- **Performance/tables (N16–N18):** visibility changes avoid GeoJSON rebuilds and redundant filters; load geometry, section chains, complete load results and detours are cached at their appropriate invalidation boundaries; demand/volume requests are debounced; one-road load updates are optimistic; only changed road feature states are painted. Tables have persistent resizing handles/keyboard resizing and reset; issue text can be expanded.
- **Analytics (T2/N19):** sections between non-degree-2 nodes, maximum z per section, explicit truck/bus PCU assumptions, time-of-day/weather factors, category filter and `uds:category` override. The OSM/category mapping is labelled as an assumption, not an engineering classification determination.
- **Rendering/help (N20–N22/T3):** default lane tags for drawn roads; lane direction arrows; display-only line smoothing and local width transitions; road/path subgroups; eight-chapter help with screenshots and tool shortcuts sourced from `TOOLS`.

## Remaining gaps / review priorities

1. **Not every original acceptance criterion is implemented.** The segment editor currently offers tag editing instead of the complete single-way attribute inspector. Its displayed length and dedicated selection highlighting need finishing. Verify relation rewriting for more complex restrictions as well as the tested route case.
2. **Multiselect needs broader browser acceptance:** three-road template/undo, grouped dragging, Shift rectangle, and the building-part Ctrl-click exception. Backend group/segment invariants are tested; the full interaction matrix is not.
3. **Catchment UI:** route-direction stop-set helper and cycling stop markers directly on the map remain to be completed. The legacy stop popup now labels its 400 m radial building/population estimate; it is separate from network catchments and is not ID5 population.
4. **Routing diagnosis:** nearest-component connectors currently compare nodes, not every node-to-segment candidate. Related existing validator cards are not yet appended. Walking-only missing-sidewalk explanations, two-second diagnosis budgets across all diagnostic branches, and combinations of blocking restrictions need refinement. Individually effective restriction removals are tested before being suggested. Via-way restrictions remain explicitly skipped.
5. **PT:** no real ID7/passport files were supplied, so do not claim real historical headways. Route chaining is an MVP; inspect decreasing chainages, stop/stop_position duplicates and clipped route order. `boundary_points` is presently an empty collection. Explanations are sampled (up to 100 walk vertices), not arbitrary-point interpolation. Add more endpoint tests for parallel routes and transfers.
6. **Map comparisons:** original paths/bands and non-selected detours are translucent, not fully differentiated dashed layers; uncovered areas are grey rather than hatched. Expand the legend and verify polygon ordering.
7. **Performance targets:** the fragment's 100 ms repaint target was not met in the recorded headless software-rendering run. Measurements below include competing QA jobs. Repeat on an idle environment after the latest redundant-filter fix. City load edits improved substantially, but remain above 200 ms in the API measurement. Cold city analysis still costs seconds. Optimistic updates need rejection/race acceptance testing.
8. **Rendering:** width transitions and smoothing are visualization approximations; validate 3→2 lanes and 90° bends at zoom 17+. OSM geometry is deliberately unchanged. Verify no marking gaps at shared junctions.
9. **Browser scope:** route, car isochrone and PT bands were exercised on the fragment with zero uncaught JS errors. Help opening/Escape, layer controls and screenshots were checked. Remote base tiles/fonts intermittently failed in the cloud browser; captures use no base map. Full production acceptance was not run.
10. **Sources:** climatic subregion IВ was not independently verified against СП 131 figure A.1; UI avoids claiming it as verified. Add verified source links where current scenario explanations only name a document.

## Validation evidence

Use Python 3.12 / Node 22. The project venv is `backend/.venv` and frontend dependencies are installed.

```bash
backend/.venv/bin/ruff check backend
cd backend && .venv/bin/python -m pytest -q
cd frontend && npm run lint && npm test && npm run build
backend/.venv/bin/python backend/scripts/bench.py data/example.osm
backend/.venv/bin/python backend/scripts/bench.py data/irkutsk_city.osm
```

The last full suite before the final refinements passed **151 backend tests**, **28 utility tests + 11 DOM tests**, typecheck and production build. A subsequent focused backend run after the geometry-cache/closure changes passed **26 tests**. Fresh full checks are run again for the PR; consult its current CI rather than treating these historical counts as a guarantee for a later commit.

New tests cover snaps, one-way diagnosis, restrictions/skips, read-only route/probe/PT responses, walking filters, catchment partial lengths/intersection, segment relation rewrite/undo, group movement, pedestrian info checks, signal seconds/arrow round-trip, tram placement, load-cache behavior, diverse closure choices, section aggregation, PCU/weather factors, selection reducers, table persistence, panel close and menus/help.

### API/load timings (milliseconds, in-process TestClient)

| File | Cold load | Cached GET | One-road volume change |
|---|---:|---:|---:|
| example.osm, before geometry/section caching | 86.6 | 4.7 | 32.4 |
| example.osm, after | 187.5 | 13.4 | 20.0 |
| irkutsk_city.osm, before | 2000.4 | 154.6 | 2171.6 |
| irkutsk_city.osm, after | 2063.0 | 149.4 | 262.9 |

These are single runs with concurrent QA activity, not statistically controlled before/after claims.

Layer toggles rebuilt **zero** `ways`/`areas`/`nodes` sources. Earlier browser median double-animation-frame times: fragment **504.8 ms**, city **203.9 ms**; repeat after the latest filter/painting changes.

### Network benchmarks / Docker

Local fragment: open **1.29 s**, validate **0.43 s**, preview **0.02 s**, SUMO **0.94 s**, export **0.16 s**. Counts at that point: **4 critical / 24 warning / 73 info**, **4 components**; SUMO **0 errors / 64 warnings**. Pedestrian info counts may increase with the later complete-segment sidewalk detection.

Local city: open **4.78 s**, validate **78.98 s**, preview **1.11 s**, SUMO **47.14 s**, export **2.33 s**. Counts at that point: **77 / 1013 / 3627**, **73 components**.

A Docker image built successfully with the repository Dockerfile plus temporary BuildKit CA-secret mounts for this cloud proxy (not committed). City benchmark in that image, with current source bind-mounted: open **4.59 s**, validate **90.02 s**, preview **0.20 s**, SUMO **18.01 s**, export **0.91 s**; SUMO **0 errors / 67 warnings**. This build/benchmark preceded the final caching and diagnostic refinements. CI must validate the final commit.

Baseline `6ca3e6f` city without SUMO, under concurrent load: open **11.31 s**, validate **74.69 s**, preview **0.65 s**, export **0.89 s**, counts **77 / 1013 / 3264**. Do not directly attribute timing differences from these concurrent single runs to a code change.

## Workspace / next steps

- New core files: `backend/app/routing.py`, `backend/app/pt_routing.py`, `backend/app/data/pt_headways.json`.
- Main new UI: `AnalysisTools.tsx`, `SelectionEditor.tsx`, `ResizableTable.tsx`, `EditorMenus.tsx`, `PanelClose.tsx`.
- Help captures: `frontend/public/help/editor.png`, `analysis.png`.
- Local QA servers were started on ports 8000 and 5173. They are development processes, not deployment.
- A detached baseline worktree exists at `/tmp/transport-backlog-baseline`; preserve/remove it through normal Git worktree commands as needed.
- No PR was merged and no production state was modified.

## PR creation update

Draft PR: https://github.com/nikitagricanuk/transport-hack-2026/pull/23

Fresh checks on implementation commit `86d456b`: **154 backend tests passed; 28 utility + 11 DOM frontend tests passed; Ruff, TypeScript and production build passed**.

At PR creation, `main` had advanced to `528bba4` through PR #22 (`feat/handoff-analysis`), which independently adds overlapping analysis work. A read-only `git merge-tree` check confirmed content/add-add conflicts, including routing, AnalysisTools, API/main, validation and related UI/tests. This draft intentionally preserves the implemented branch for review; it has **not** reconciled the newer main, merged, or deployed. Next integration work must compare the two implementations and retain the accepted changes from main rather than overwriting them.

## Reconciliation update — continuation

The working branch now incorporates `main` at `528bba4`. The accepted `routing.py`, typed analysis requests, `network_analysis.py`, `pt_analysis.py`, `AnalysisTools` / `PtTools`, and analysis picking/overlay contracts are canonical. The duplicate `pt_routing.py` and frontend `utils/analysis.ts` implementations were removed. Editor, signal, closure alternatives, section/load caching, table sizing, and Help/Reports changes are retained. This merges main **into the PR branch**, not the PR into production.

Additional work completed during integration:

- Closure alternatives now use the canonical restriction structure; the diagnosed reverse-way fix uses the correct operation argument.
- Loaded-speed isochrones include the weather/composition settings and selected closure scenario.
- Segment editing reuses the single-way attribute controls (including lanes, speed, classification and bridge), shows length and a dedicated map highlight, previews all changes atomically, then selects the resulting middle segment after Apply.
- Group common-tag lookup supports areas/buildings, and road templates only target road objects.
- Map size tracks its container with ResizeObserver, including panel size/visibility changes.
- Shared frontend source imports support the existing native Node test runner via TypeScript's no-emit `allowImportingTsExtensions` option.

Verification after reconciliation: **156 backend tests; 33 utility + 11 DOM tests; Ruff, TypeScript and production build pass**. The user explicitly requested no further browser checks; subsequent verification is unit tests, lint and build only. Do not treat the attempted group/segment browser workflow as completed acceptance.

Remaining implementation work is concentrated in routing diagnosis refinements, robust handling of rejected/overlapping optimistic load requests, final source/assumption documentation, and refreshing the PR description/CI. The earlier statements that reconciliation and the full segment inspector were missing are superseded by this update.
