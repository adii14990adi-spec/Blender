# Browser test results

Generated 2026-11-12T15:34:18.959Z against `http://127.0.0.1:8123/Blender/` with chromium.

**14 passed, 0 failed**

| # | Scenario | Result | Time | Notes |
| --- | --- | --- | --- | --- |
| 1 | mobile · shell renders from the manifest | pass | 4.0 s | 2 characters · 2 outfits · 2/2 validated |
| 2 | mobile · GLB loads and renders | pass | 0.0 s | outputs/male/jacket_urban_01/jacket_urban_01_Modular.glb → 394×401 |
| 3 | mobile · character switch without a reload | pass | 0.9 s | outputs/female/jacket_urban_01/jacket_urban_01_Modular.glb |
| 4 | mobile · outfit library switches builds | pass | 1.2 s | jacket_urban_01 → parka_urban_01 |
| 5 | mobile · view panel exposes camera + animation state | pass | 0.2 s | 5 presets |
| 6 | mobile · camera reset, auto-rotate and fullscreen | pass | 1.6 s | native fullscreen |
| 7 | mobile · download links point at real files | pass | 0.2 s | 2 files · 1011.4 KB / 767.4 KB |
| 8 | mobile · no failed requests or console errors | pass | 0.0 s | 30 requests, all ok |
| 9 | mobile · animation transport works when clips exist | pass | 6.2 s | 1 clip(s): AnimatedCubeAction |
| 10 | mobile · missing manifest shows an actionable error | pass | 0.4 s | fixtures/manifest.missing.json → HTTP 404 Not Found. Deploy the site with tools/ |
| 11 | mobile · empty manifest renders the empty state | pass | 0.2 s | No builds published yet |
| 12 | desktop · sidebar + stage + properties layout | pass | 4.1 s | columns 300 / 768 / 372 px |
| 13 | mobile · deep link selects character, outfit and camera | pass | 4.4 s | outputs/female/parka_urban_01/parka_urban_01_Modular.glb |
| 14 | deployment · every request stays under the project subpath | pass | 3.6 s | 9 requests, all under /Blender/ |

Screenshots: `screenshots/`
