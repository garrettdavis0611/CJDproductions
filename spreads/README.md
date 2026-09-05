# SPREADS Board

Rebuilds the NCAAF/NFL betting board every 15 minutes and emails it as a PDF
when games kick within the next ~20 minutes. This is a port of the Google Apps
Script that ran on `SPREADS Board` in Drive, with the line-loss bug fixed.

## Why this exists

The Apps Script version was stateless: it wiped the sheet and re-rendered only
what the APIs returned right then. Pinnacle stops quoting a matchup the moment
it goes in-play, so every spread disappeared at kickoff and never came back —
which is why whole day-tabs ended up blank. This version remembers the last
good line per game and keeps showing it, marked as stale.

## Rules (unchanged from the script)

- **Slate** comes from ESPN — NCAAF is FBS only (`groups=80`), plus NFL.
- **Lines** are aggregated: Pinnacle's main line and juice, blended with
  Kalshi's alt-line ladders. When the two disagree by a point or more, whichever
  source priced closest to a balanced -110/-110 market wins.
- Shaded Pinnacle juice (a side at roughly -118 or worse) nudges the number half
  a point.
- Half-point spreads snap to adjacent key numbers {3, 7, 9, 14, 21, 28}; the
  rest keep the .5.
- A -1 spread prints on both sides as a pick'em.
- Rotation numbers run 1..2N for teams, then continue for O/U.
- Email fires once per kickoff wave, ~20 minutes out, deduped by kickoff time.

## What changed

| Behaviour | Apps Script | Here |
| --- | --- | --- |
| Line at kickoff | lost when Pinnacle drops the game | restored from cache, shown italic |
| Started games | rows hidden | greyed, still on the board |
| Pick'em (spread 0) | discarded as falsy | kept as a real line |
| Kalshi failure | silently swallowed | logged per league |
| Output | Google Sheet tab + PDF | HTML + PDF (no Sheets dependency) |

## Setup

Add these under **Settings → Secrets and variables → Actions**:

| Secret | Required | Notes |
| --- | --- | --- |
| `SMTP_HOST` | yes | e.g. `smtp.gmail.com` |
| `SMTP_PORT` | no | defaults to 465 (implicit TLS) |
| `SMTP_USER` | yes | the sending account |
| `SMTP_PASS` | yes | for Gmail, an **App Password**, not your login |
| `MAIL_TO` | no | defaults to the address the script used |
| `MAIL_FROM` | no | defaults to `SMTP_USER` |
| `PINNACLE_KEY` | no | override if the public guest key rotates |

Optional repo **variable** `KICKOFF_WINDOW_MIN` (default 20) widens the send
window — useful because GitHub's scheduler can run several minutes late.

## Running locally

```bash
npm ci
npm test                  # 26 unit tests, no network needed
npm run dry-run           # builds out/board.html, prints the email instead of sending
npm start                 # builds and sends for real
```

`--force-email` sends regardless of kickoff times, for testing SMTP.

## State

`state.json` holds last-known lines (expiring after `LINE_CACHE_TTL_H`, default
30h) and which kickoff waves were emailed. In CI it rides in the Actions cache.
If that cache is evicted the worst case is one duplicate email and a few lines
falling back to blank until the next live quote.

## Known limits

- The Pinnacle guest key is public and rotates; a league-wide loss of lines
  usually means it changed.
- ESPN and Pinnacle name matching is fuzzy (Dice bigrams, 0.5 threshold). A game
  whose names diverge badly on both sides gets no line.
- GitHub's cron is best-effort. Widen `KICKOFF_WINDOW_MIN` if emails are missed.
