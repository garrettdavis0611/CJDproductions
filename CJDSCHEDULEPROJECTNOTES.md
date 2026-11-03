# CJD Production Schedule — Project Handoff Notes

_Last updated: July 28, 2026. This document contains everything needed to continue work on the CJD crew-scheduling system in a new session (Claude Code or otherwise)._

## What this project is

Garrett runs a production business (CJD) staffing live sporting events. The system has two parts:

1. **A master Google Sheet** — the editable source of truth. Crew members edit it (assign who's working each game) via Google Sheets.
2. **A single-file mobile-friendly web page** (`index.html`) — a read-only, color-coded schedule view that pulls live data from the sheet every time it's opened. Being deployed to GitHub Pages.

## Live resources & IDs

| Resource | Value |
|---|---|
| Google Sheet (CURRENT, keep) | "Master Production ScheduleFinal" — ID `1f87gUR8wuRIukrP7muhQxP0tVa3Ja2RR9eg7xZoSZjk` — https://docs.google.com/spreadsheets/d/1f87gUR8wuRIukrP7muhQxP0tVa3Ja2RR9eg7xZoSZjk/edit |
| GitHub repo | `garrettdavis0611/CJDproductions` (public, default branch `main`) |
| Intended Pages URL | https://garrettdavis0611.github.io/CJDproductions/ |
| Old sheets to TRASH (stale iterations) | "Master Production Schedule" ×2 (`1gWc60aS2XXsMdx1yvFuY9HILJyKo8_ze_vnFDK_7bek`, `1-RgBkksyMn3Z4InHFZwGdpgw31XIWyzSUVI9d3al1B8`), "Production Schedule – BC Volleyball" (`1FyN8qti0...Y7E`), "Production Schedule" (`1RP0yHYz...pq-w`) |

## Current status

✅ **DEPLOYED July 28, 2026.** https://garrettdavis0611.github.io/CJDproductions/ is live and loading real data from the sheet (verified: page returns 200, gviz CSV endpoint reachable). `index.html` was committed to the repo root and the misnamed `productionschedule (1).html` was deleted. Pages source: `main` → `/ (root)`.

Optional next: add a custom domain (CNAME record `schedule` → `garrettdavis0611.github.io` at the registrar, then Settings → Pages → Custom domain → Enforce HTTPS).

## Google Sheet structure

Tab 1 **"Schedule"** (must remain the FIRST tab — the page reads the first tab):

Columns: `Date` (YYYY-MM-DD) | `Day` | `Team` | `Event / Job` | `Location` | `Call Time` | `Start Time` | `Crew 1`…`Crew 5` | `Notes`

- No End Time column (per Garrett's request).
- Call time rules (updated Jul 28, 2026 — replaces old blanket 2h rule): **3 hours before start** for RIFC, Boston Legacy, BC Football, men's hockey (BC + NU), ALL men's & women's basketball (BC + NU), and Northeastern Women's Hockey. **2 hours before start** for all volleyball (BC + NU) and BC Women's Hockey. TBD start ⇒ TBD call.
- Crew 1–5 cells have **dropdown data validation** with an inline list: `Garrett, Adrian, Kadden, Matt, Anthony`, applied to range H2:L300. (Inline list, NOT a range reference — Google's xlsx→Sheet conversion strips range-based validation but keeps inline lists. This was learned the hard way.)
- To change roster names: select H2:L300 → Data → Data validation → edit the rule's list (or regenerate the sheet).
- Rows are color-tinted by team; Team cell is solid team color.

Tab 2 **"Roster"** — reference list of crew names only (not wired to the dropdowns).

Sharing: **"Anyone with the link — Viewer"** (flipped July 28, 2026; verified the public page's CSV fetch still works). ⚠️ Crew members' emails still need to be invited as Editors so they can assign themselves to games — emails not yet collected from Garrett.

## Aug 19, 2026 refresh — BC hockey slates official (132 games total)

- **BC MIH full schedule went official Aug 4** (bceagles.com): our 10 provisional HE dates were all exactly right; 5 NEW home games added (Oct 30 Northeastern, Nov 5+6 Vermont, Feb 12 UConn, Feb 20 Merrimack — Garrett has all but Feb 20, which is Adrian's after his 2PM NU MBB game). Nearly all MIH starts are 7:00 PM (4:00 PM call); only Oct 17 St. Cloud is still TBD.
- **BC WIH Hockey East home slate found via hockeyeastonline.com composite** (NOT yet on bceagles.com — dates could shift when BC publishes; noted per-row): 12 NEW home games split Kadden/Anthony (BC WIH is the one hockey they may work). All WIH times TBD.
- **Basketball: NO new games.** ACC dated schedules are a September release (BC will HOST Clemson, Duke, FSU, Georgia Tech, Louisville, UNC, Notre Dame, SMU, Virginia Tech in MBB — expect ~9+ more Conte games then; WBB ACC matchups not even announced). MBB times still TBA. MBB Nov 6 vs UMass is at TD Garden — correctly excluded.
- Shared-venue nights to watch when WIH times land: Oct 30, Jan 8, Feb 12 have MIH+WIH both at Conte.
- **Season totals: Garrett 61/$31,100 · Adrian 31/$12,900 · Kadden 21/$8,650 · Anthony 14/$5,200 · Matt 5/$1,750.**

## Nov 3, 2026 — time-fill pass (BC + NU) + feed hardening

- **30 TBD start/call times filled** from official sources (bceagles.com schedule pages + NU's marketing grid). All 18 BC ACC basketball games now have times. Nov 7 BC Football vs FSU = 12:00 PM (9:00 AM call).
- **Providence MIH home game MOVED Jan 16 → Jan 15, 2027 (Friday)** per bceagles.com — Jan 16 is now AWAY at Providence. Row updated with a note.
- **Feb 20 NU WIH vs Merrimack is at Lawler Arena (Merrimack College, North Andover)** per NU's grid — 8:00 PM start, 5:00 PM call. Travel night, not Matthews.
- **Remaining TBDs (3):** Nov 21 BC Football vs Syracuse (TV window not out); Feb 13 NU WIH vs BC (blank on NU's grid too); Mar 6 NU MIH vs BC (TBA — and notably REMOVED from NU's marketing grid; worth asking NU whether that game still exists).
- Master is at **151 games** (rows 2–152). New crew name "Kenny" appears in Garrett's own assignments.
- Sidearm/bceagles CDN gotcha: it intermittently serves ANOTHER school's schedule page on BC URLs (served Utah's football page once). Any automated fetch must validate the page says "Boston College" / the right venue before parsing.
- Feed hardening: site + widget switched to the export CSV endpoint (see gotchas below); FALLBACK_CSV snapshot refreshed to the 151-game state; both deployed to the repo.

## Rates (per game, given by Garrett Jul 28, 2026 — used for scheduling decisions)

| Team | Rate |
|---|---|
| BC Football | $850 |
| Rhode Island FC | $650 |
| BC Men's Hockey | $500 |
| BC Men's Basketball | $500 |
| BC Women's Basketball | $500 |
| Northeastern Men's Hockey | $500 |
| Northeastern Women's Hockey | $500 |
| Northeastern Men's Basketball | $400 |
| Northeastern Women's Basketball | $400 |
| BC Volleyball | $350 |
| Northeastern Volleyball | $350 |
| BC Women's Hockey | $350 |
| Boston Legacy FC | $650 |

**Scheduling constraints (from Garrett):**
- Adrian cannot work Rhode Island FC or Boston Legacy FC games (i.e., nothing at Centreville Bank Stadium).
- Garrett: must work ALL BC Football; maximize money/time at Legacy + RIFC + all BC teams; max 1 weekend day/week ("if possible" — exceptions kept: Aug 8+9, Sep 19+20, both top-venue weekends).
- Adrian: maximize, Northeastern-first, gets the bigger NU share ("extra juice") incl. both $500 NU hockey games.
- Matt: smaller NU share.
- Kadden: BC/NU overflow that Garrett's rules exclude (weekend leftovers, same-day conflicts).
- Anthony: role unclear — Garrett hasn't said; has only Sep 5 BC VB. (Aug 2026 rebalance later gave him 8 games.)
- **ALL hockey games are Garrett/Adrian ONLY — EXCEPT BC Women's Hockey, which anyone may work** (rule expanded Aug 19, 2026: Kadden/Anthony/Matt never on BC MIH or any NU hockey; Oct 17 BC MIH moved Kadden→Adrian accordingly).
- **WHITELISTS (Aug 19, 2026, final): Kadden may ONLY work BC Volleyball, BC Women's Hockey, NU Women's/Men's Basketball, NU Volleyball, AND RIFC/Legacy. Anthony may ONLY work BC Volleyball, BC Women's Hockey, NU Women's/Men's Basketball, NU Volleyball (NO Centreville).**
- **Matt is OFF the schedule entirely (Aug 19, 2026, final pass)** — his last 5 games redistributed: Aug 22 BC VB → Anthony, Sep 4 NU VB → Adrian, Sep 26 NU VB → Kadden, Oct 9 NU VB → Anthony. Sep 5 lineup set explicitly by Garrett: Garrett BC VB 2PM, Anthony NU VB 5PM, Kadden RIFC 7:30PM. Matt: 0 games.
- **Centreville Bank Stadium games (RIFC + Boston Legacy) = Garrett or Kadden ONLY. Matt is NOT allowed at Centreville.** Enforcement history: Kadden briefly lost Sep 5 + Oct 17 RIFC to Garrett during the first whitelist pass, then both were restored to Kadden when Garrett clarified Centreville eligibility; Nov 15 + Nov 22 BC MBB stayed moved from Kadden to Adrian (BC MBB remains off Kadden's list).
- Garrett is happy to take NU weekday games when free (took Dec 2 + Feb 4 NU MBB, Feb 5 NU WBB in Aug 19 pass).
- **Northeastern HOCKEY games (M+W) may only be staffed by Garrett or Adrian** (rule added Aug 19, 2026). Garrett takes all BC-vs-NU hockey matchups (Oct 31, Dec 3, Feb 13, Mar 6) plus Feb 20 WIH UConn; Adrian covers the other six NU hockey games. Season totals after this: Garrett 54/$27,900 · Adrian 30/$12,300 · Kadden 18/$7,850 · Anthony 8/$3,100 · Matt 5/$1,750.

**UPDATE Aug 18, 2026 — NU marketing grid intel merged (115 games total).** Added 19 games from NU's own sheet: full NU WIH slate (9 games — "home" venues are Walter Brown Arena (BU) and Bentley Arena; Matthews demolished), 2 NU MIH games (both vs BC, venue TBA), 5 MBB + 5 WBB non-conference. Corrections: NU WIH opener is Sun Oct 11 (not Oct 10); MBB Feb 11 opponent is Towson (not Hofstra). All 18 CAA basketball start times filled from NU's grid (3h calls). Rebalance per Garrett: Matt cut to 5 games (through Oct 9); his later NU slots went to Adrian/Kadden/Anthony. Season totals: Garrett 49/$25,400 · Adrian 33/$13,800 · Kadden 20/$8,850 · Anthony 8/$3,100 · Matt 5/$1,750. (Garrett had also self-assigned Kadden to Sep 5 + Oct 17 RIFC in August — preserved.)

**Original Jul 28, 2026 assignment pass** (95 of 96 games): Garrett 50/$26,350 · Adrian 19/$7,450 · Matt 16/$6,000 · Kadden 9/$3,600 · Anthony 1/$350. UNASSIGNED: Oct 17 RIFC 7:30 PM (Garrett at BC Football that day; pending kickoff time — Garrett could stack if early kickoff, else Anthony/Matt/Kadden; NOT Adrian). Provisional TBD-time stacks that may need reshuffling: Garrett Oct 25 (BC VB 1PM + MBB MIT), Garrett Nov 14 (WBB 12PM + MIH), Kadden Nov 15 (BC VB 12:30 + MBB UNH). Assignment logic archived in scratchpad assignments.json (session-local); rates + rules above are the source of truth.

## Schedule contents — winter sports added Jul 28 (96 home games total, 12 teams)

54 winter-sport rows added July 28, 2026 (rows 44–97; sorted client-side by the page, so sheet order doesn't matter). All researched from official sites with cross-checks. Teams: BC Men's/Women's Basketball (Conte Forum), BC Men's/Women's Hockey (Kelley Rink at Conte Forum), Northeastern Men's/Women's Basketball (Cabot Center), Northeastern Women's Hockey (venue TBA).

**Winter-sport caveats (all schedules partial as of Jul 28, 2026):**
- **BC MBB (10 games)**: non-conference only (BC release Jul 24) incl. MIT exhibition Oct 25. ACC home slate (9 opponents known, dates TBD) not yet released. All times TBA. Excluded: Nov 6 vs UMass at TD Garden (not Conte Forum).
- **BC WBB (7 games)**: non-conference only (BC release Jul 22), times published (could shift with TV). ACC slate TBD.
- **BC MIH (11 games)**: 4 official non-conference (Alaska, St. Cloud ×2, Stonehill — CHN calls Stonehill an exhibition, BC doesn't); 7 Hockey East home dates taken from OPPONENTS' schedules (BC hasn't released HE slate) — marked "date unofficial" in Notes; more HE home games expected (Merrimack, NU, UConn, Vermont unpublished). Beanpot (TD Garden) excluded.
- **BC WIH (6 games)**: official non-conference incl. hosting Women's Beanpot opening round Jan 12 vs BU at Kelley Rink (Jan 19 final is TD Garden — excluded). Full Hockey East slate TBD.
- **NU MBB/WBB (9+9 games)**: CAA conference slates only (released ~Jul 16), all Cabot Center, times TBA. Non-conference + exhibitions TBD.
- **NU hockey**: ⚠️ **Matthews Arena was demolished (deconstruction began Mar 2026; replacement opens 2028-29). NU has NOT announced its temporary home rink or its 2026-27 hockey schedules** (last year they released Oct 17!). Only 2 WIH home dates known (via UNH's schedule), venue TBA. Zero MIH home games verifiable — men's published opponents all host NU. Re-check nuhuskies.com in Sept–Oct.
- gviz gotcha (hit Jul 28): pasted dates became DATE-typed cells while original rows were text → gviz folded the original rows into a mega-header. Fix: the pasted range A44:M97 was formatted Plain text before re-pasting. **Any future rows added to the sheet: format the range Plain text first (or type an apostrophe before dates), keeping column types consistent.**

## Original schedule contents (42 games, summer/fall teams)

Data pulled July 27, 2026 from official sources. All games at each team's home venue; call time = start − 2h; TBD where kickoff not announced.

- **BC Volleyball** (17 games, Margot Connell Recreation Center, gold) — incl. Aug 22 Providence exhibition; Sep 9 Vanderbilt game time TBD ("Showdown at the Net"). Source: bceagles.com/sports/womens-volleyball/schedule
- **BC Football** (6 games, Alumni Stadium, gold) — Sep 11 Rutgers 7:30 PM, Sep 19 Maine 2:00 PM; Sep 26 VT / Oct 17 Pitt / Nov 7 FSU / Nov 21 Syracuse kickoffs TBD (TV windows — recheck bceagles.com ~2 weeks out).
- **Northeastern Volleyball** (11 games, Cabot Center, red) — neutral-site "vs" games (Columbia SC, Madison County NY tournaments) intentionally excluded. Source: nuhuskies.com
- **Rhode Island FC** (6 remaining home games, Centreville Bank Stadium, blue, all 7:30 PM) — Aug 8 Colorado Springs, Aug 22 Monterey Bay, Sep 5 Louisville City, Sep 30 Indy Eleven (Wed), Oct 14 Loudoun United (Wed — rescheduled home opener), Oct 17 Pittsburgh Riverhounds. Past 2026 home games excluded. USL playoffs not yet scheduled. Sources: club press release + FotMob (FotMob was the tiebreaker for conflicting home/away info).
- **Boston Legacy FC** (2 remaining Centreville home games, green) — Aug 9 Portland Thorns 4:00 PM, Sep 20 Houston Dash 2:00 PM. NOTE: their Aug 31 (Angel City) and Oct 16 (Racing Louisville) home games are at **Gillette Stadium** and were excluded per Garrett's "Centreville only" instruction.

Crew assignments entered by Garrett so far: Crew 1 filled for all Aug 8 – Sep 19 games (Garrett/Adrian/Kadden/Matt/Anthony mix); Sep 20 onward unassigned.

## index.html architecture (single self-contained file)

- Header "CJD Production Schedule", refresh button, **Edit** button → opens the Google Sheet.
- Fetches live CSV from `https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid=353392139` (switched from gviz Nov 3, 2026 — the export endpoint is immune to the date-typing bug and serves CORS `access-control-allow-origin: *`; gid is the Schedule tab's id). Falls back to an embedded `FALLBACK_CSV` snapshot (with yellow banner) if unreachable — snapshot refreshed Nov 3, 2026 (151 games incl. all time fills).
- Config at top of the script: `SHEET_ID`, `TEAM_COLORS` (regex → color; legacy/green `#00843D`, northeastern/red `#C8102E`, rhode island/blue `#1D4ED8`, BC/gold `#B7962B`; matching order matters — "legacy" before the BC/boston rule). Unknown teams get neutral gray.
- Parser: flexible header matching (normalized lowercase alphanumeric); reads either `Who's Working` or `Crew 1`–`Crew 5` columns (crew columns joined into one list); missing End Time is fine.
- UI: day cards grouped by date (today highlighted, past games hidden the day after they're played), per-shift team badge + left color stripe, call/start time boxes, crew chips, notes. Two filter rows (added Jul 28): top row = All + school/org chips (`SCHOOLS` config maps teams → "Boston College", "Northeastern", "Rhode Island FC", "Boston Legacy FC" — one chip covers all that school's sports); second row = crew-name chips from the `ROSTER` config (always visible, plus any extra names found in data). School + person filters combine.
- Dark mode via `prefers-color-scheme`. iOS "Add to Home Screen" friendly.
- Calendar view (added Jul 28): List/Calendar segmented toggle above the filters. Month grid with ‹ › nav, weekday headers, team-color dots on game days (max 4 dots/day), today outlined, past days dimmed; tapping a dotted day shows that day's games below the grid (reuses `dayHeadHTML`/`shiftHTML` helpers shared with the list). School/crew filters apply to the dots and day panel. Calendar shows past games (dimmed) since it's a month overview; the list still hides them the day after. State: `state.view`, `state.calM` (displayed month), `state.selDay`.
- Visual design (redesigned Jul 28): dark branded header (real CJD Productions camera logo — cropped from `CJD_Productions_Hoodie_Logo_300DPI.png` in ~/Downloads, embedded as a base64 data URI so the file stays self-contained; repo also has `apple-touch-icon.png` + `favicon.png` flattened on white from the same logo), condensed uppercase title, 4-team-color stripe under header, Google Fonts (Barlow Condensed display + Inter body — degrades to system fonts offline), month divider labels, calendar-style date badges (accent-filled on today, "Tomorrow" outline tag), team-color tint gradient on each shift row, crew chips with colored initial avatars (`PERSON_COLORS` — roster order: Garrett blue, Adrian pink, Kadden green, Matt amber, Anthony violet; same colors as the filter-chip dots), notes in inset boxes, pulsing green "Live" dot in the footer stamp.
- To repoint at a different sheet: change `SHEET_ID` only. To add a team color: add a line to `TEAM_COLORS`.

## iOS widget (added Jul 28, 2026)

`cjd-widget.js` in the repo root — a Scriptable (scriptable.app, free App Store app) home-screen widget. Fetches the same export-endpoint CSV live (switched from gviz Nov 3, 2026); widget Parameter = crew name filters to that person's shifts (empty = all games). Small = next shift w/ call time; medium = next 3; large = next 7. Team-color dots, dark/light aware, tap opens the site, auto-refreshes ~30 min. Install: copy script from https://raw.githubusercontent.com/garrettdavis0611/CJDproductions/main/cjd-widget.js into a new Scriptable script named "CJD Schedule", add Scriptable widget, set Parameter to your name. Data logic (parseCSV/toRecords/pickShifts) is node-testable — the Scriptable-API section is guarded behind `typeof config !== "undefined"`. A native WidgetKit widget was considered but requires an Xcode app + TestFlight/dev account to distribute to crew.

## Northeastern sheet auto-sync (added Aug 18, 2026)

**Rule: edit NU games only in the MASTER sheet — never in NU's grid directly.** An Apps Script ("nu-sync", bound to the master sheet via Extensions → Apps Script; copy archived as `nu-sync.gs` in the GitHub repo) watches edits on the Schedule tab: any change to a Northeastern row pushes the crew name(s) into the matching game's "Videoboard Producer" cell in NU's "Hockey & Basketball Marketing Grid 2026-27" (`1-Cq941_dfPA8j01j9gN0Jm1yOEpXfEeZMiOoyIG3qQo`). Matches by tab (headers, not names), date ("November 10" style), and team (hockey tab). `syncAll()` in the same project backfills every NU row at once. Crew only — game times/dates stay NU's.

✅ **LIVE as of Aug 19, 2026.** NU granted Editor access Aug 19; `setup` was re-run and the backfill wrote **all 50 Northeastern games** into the Videoboard Producer column across all four tabs (verified in the UI, incl. the three rebalance corrections: Oct 10 VB → Adrian, Oct 25 VB → Kadden, Nov 15 VB → Anthony). Debug history worth keeping: (1) needed explicit `oauthScopes` (spreadsheets + drive + script.scriptapp) in appsscript.json for `openById` on the link-shared file; (2) tab matching is by TAB NAME first (Hockey / Men's Basketball / Women's Basketball / Volleyball) with header heuristics as fallback — the original "Post Set 1" header sniff silently failed for the volleyball tab; (3) each write/no-match is console-logged, so the Executions page shows exactly what synced. Per-edit auto-sync is active: change any NU row in the master and their sheet updates within seconds.

## Known constraints & gotchas (hard-won)

- The Google Drive connector in Cowork **cannot edit existing sheets in place** — every data change means creating a NEW sheet (new ID) and updating `SHEET_ID` in index.html, OR editing the sheet manually in the Sheets UI.
- Google's file converter **strips range-based data validation** from uploaded xlsx; **inline-list validation survives**.
- The Cowork chat-panel preview cannot reach Google, so it always shows the fallback snapshot (with banner). Only real browsers show live data. Don't chase "it's not updating" reports without asking where they're viewing it.
- Email apps (Gmail etc.) won't render an HTML attachment when tapped — that's why GitHub Pages hosting.
- ~~gviz CSV endpoint reads the FIRST tab — keep "Schedule" first.~~ Site + widget moved to the `/export?format=csv&gid=353392139` endpoint Nov 3, 2026, which pins the Schedule tab by gid AND is immune to the date-typing bug (gviz broke a third time Nov 3 — only 58 of 151 rows served — after date cells got date-typed again). The sheet's column A was also re-pasted as Plain text that day. Dates in column A should still be typed/pasted as plain text (apostrophe prefix or pre-format Plain text) because the widget/site date regex expects `YYYY-MM-DD` text, but a slip no longer silently drops rows from the feed.
- BC volleyball schedule pages garble easily when summarized — verbatim row-by-row transcription prompts were needed; verify with a second source when refreshing schedules.

## Pending / possible next tasks

1. ~~Finish GitHub Pages deploy~~ ✅ Done July 28, 2026 — site live and verified.
2. ~~Flip sheet sharing to Viewer-link~~ ✅ Done July 28, 2026. Still TODO: invite crew emails as Editors (need emails for Adrian, Kadden, Matt, Anthony).
3. ~~Trash the 4 stale Google Sheets~~ ✅ Done July 28, 2026 (all 4 in Drive trash, recoverable 30 days).
4. Optional: custom domain.
5. Later in season: fill in BC football TBD kickoffs, Sep 9 Vanderbilt time, RIFC/NWSL playoff games; snapshot refresh in index.html to match.
5a. Winter-sport follow-ups: BC/NU ACC+CAA non-conference gaps and all TBD times; BC Hockey East official slate (replace "date unofficial" rows); NU hockey schedules + temporary home venue (watch nuhuskies.com Sept–Oct); confirm BC MIH Stonehill Jan 8 (game vs exhibition).
6. Possible feature asks: business logo in header, dark theme default, per-crew share links, more teams.
