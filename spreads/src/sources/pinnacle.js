// Pinnacle guest API: main line plus juice. In-play matchups are excluded —
// Pinnacle stops serving them, which is why lines vanish at kickoff.
import { PIN_BASE, PIN_KEY, PIN_LEAGUES } from '../config.js';
import { getJson } from './http.js';

export function parsePinnacle(matchups, markets) {
  const spreads = {};
  const totals = {};
  for (const m of markets) {
    if (m.period !== 0 || m.isAlternate) continue;
    if (m.type === 'spread') spreads[m.matchupId] = m;
    else if (m.type === 'total') totals[m.matchupId] = m;
  }
  const lines = [];
  for (const mu of matchups) {
    if (mu.type !== 'matchup' || mu.isLive) continue;
    const parts = {};
    for (const p of mu.participants || []) parts[p.alignment] = p;
    if (!parts.home || !parts.away) continue;

    let homeSpread = null;
    let homePrice = null;
    let awayPrice = null;
    const sp = spreads[mu.id];
    if (sp) for (const pr of sp.prices || []) {
      if (pr.designation === 'home') { homeSpread = pr.points; homePrice = pr.price; }
      if (pr.designation === 'away') awayPrice = pr.price;
    }

    let total = null;
    let overPrice = null;
    let underPrice = null;
    const tt = totals[mu.id];
    if (tt) for (const pr of tt.prices || []) {
      if (pr.designation === 'over') { total = pr.points; overPrice = pr.price; }
      if (pr.designation === 'under') { total = pr.points; underPrice = pr.price; }
    }

    lines.push({
      away: parts.away.name,
      home: parts.home.name,
      kick: new Date(mu.startTime),
      homeSpread, homePrice, awayPrice,
      total, overPrice, underPrice
    });
  }
  return lines;
}

export async function fetchPinnacleLines(league) {
  const lid = PIN_LEAGUES[league];
  const headers = { 'X-API-Key': PIN_KEY, Referer: 'https://www.pinnacle.com/' };
  const [matchups, markets] = await Promise.all([
    getJson(`${PIN_BASE}/leagues/${lid}/matchups`, headers),
    getJson(`${PIN_BASE}/leagues/${lid}/markets/straight`, headers)
  ]);
  return parsePinnacle(matchups, markets);
}
