// Durable state across runs: last-known-good lines, and which kickoff waves
// have already been emailed. In CI this file is carried by the Actions cache.
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { dirname } from 'node:path';
import { LINE_CACHE_TTL_H } from './config.js';
import { etDateStr } from './tz.js';

const EMPTY = { lines: {}, sent: {} };

export function loadState(path) {
  try {
    return { ...EMPTY, ...JSON.parse(readFileSync(path, 'utf8')) };
  } catch {
    return structuredClone(EMPTY);
  }
}

export function saveState(path, state, now = new Date()) {
  const cutoff = now.getTime() - LINE_CACHE_TTL_H * 3600 * 1000;
  for (const k of Object.keys(state.lines)) {
    if (state.lines[k].at < cutoff) delete state.lines[k];
  }
  // Sent markers only matter for the current day.
  const today = etDateStr(now);
  for (const k of Object.keys(state.sent)) {
    if (!k.startsWith(`${today}_`)) delete state.sent[k];
  }
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, JSON.stringify(state, null, 2));
}

export function lineKey(league, g) {
  const clean = s => String(s).replace(/\W/g, '');
  return `${league}_${etDateStr(g.kick)}_${clean(g.away)}_${clean(g.home)}`;
}

/**
 * Fill blanks from the cache and refresh the cache from live lines.
 * This is what keeps a spread on the board after Pinnacle drops the game.
 */
export function applyLineCache(state, league, games, now = new Date()) {
  const cutoff = now.getTime() - LINE_CACHE_TTL_H * 3600 * 1000;
  for (const g of games) {
    const key = lineKey(league, g);
    if (g.spread === null && g.total === null) {
      const c = state.lines[key];
      if (c && c.at >= cutoff) {
        g.spread = c.spread;
        g.total = c.total;
        g.fav = c.fav;
        g.stale = true;
      }
    } else {
      state.lines[key] = { spread: g.spread, total: g.total, fav: g.fav, at: now.getTime() };
    }
  }
}
