// Kalshi alt-line ladders. Fair line per event = the strike whose yes-mid sits
// nearest 0.50, trusted only when the book is real (bid & ask, spread <= 10c).
import { KALSHI_BASE, KALSHI_SERIES } from '../config.js';
import { etDateCode } from '../tz.js';
import { getJson } from './http.js';

async function kalshiMarkets(series, datecode) {
  const out = [];
  let cursor = '';
  for (let i = 0; i < 10; i++) {
    const url = `${KALSHI_BASE}/markets?limit=1000&status=open&series_ticker=${series}` +
                (cursor ? `&cursor=${cursor}` : '');
    const d = await getJson(url);
    const ms = d.markets || [];
    for (const m of ms) if ((m.ticker || '').includes(datecode)) out.push(m);
    cursor = d.cursor;
    if (!cursor || !ms.length) break;
  }
  return out;
}

export function buildLadders(markets, kind) {
  const ladders = {};
  for (const m of markets) {
    const bid = Number(m.yes_bid_dollars || 0);
    const ask = Number(m.yes_ask_dollars || 0);
    if (bid <= 0 || ask <= 0 || ask - bid > 0.10) continue;
    const ev = (m.ticker || '').split('-')[1];
    if (!ev) continue;
    let team = null;
    if (kind === 'spread' && (m.title || '').includes(' wins by over ')) {
      team = m.title.split(' wins by over ')[0];
    }
    (ladders[ev] = ladders[ev] || []).push({ team, strike: m.floor_strike, mid: (bid + ask) / 2 });
  }
  const res = {};
  for (const ev of Object.keys(ladders)) {
    let best = null;
    for (const r of ladders[ev]) {
      if (!best || Math.abs(r.mid - 0.5) < Math.abs(best.mid - 0.5)) best = r;
    }
    if (best && Math.abs(best.mid - 0.5) <= 0.10) {
      const teams = [];
      for (const r of ladders[ev]) if (r.team && !teams.includes(r.team)) teams.push(r.team);
      res[ev] = { team: best.team, line: best.strike, dist: Math.abs(best.mid - 0.5), teams };
    }
  }
  return res;
}

export async function fetchKalshiFair(league, now = new Date()) {
  const datecode = etDateCode(now);
  const res = { spread: {}, total: {} };
  for (const kind of ['spread', 'total']) {
    const markets = await kalshiMarkets(KALSHI_SERIES[league][kind], datecode);
    res[kind] = buildLadders(markets, kind);
  }
  return res;
}
