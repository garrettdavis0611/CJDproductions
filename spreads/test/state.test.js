import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { loadState, saveState, applyLineCache, lineKey } from '../src/state.js';

function game(away, home, kick, over = {}) {
  return { away, home, kick: new Date(kick), state: 'pre',
           spread: null, total: null, fav: null, ...over };
}

test('a live line is written to the cache', () => {
  const state = { lines: {}, sent: {} };
  const g = game('Ball State', 'Ohio State', '2026-09-05T16:30:00Z',
                 { spread: 50.5, total: 53.5, fav: 'home' });
  applyLineCache(state, 'NCAAF', [g], new Date('2026-09-05T16:00:00Z'));
  assert.equal(Object.keys(state.lines).length, 1);
  assert.equal(state.lines[lineKey('NCAAF', g)].spread, 50.5);
});

test('a game Pinnacle has dropped keeps its last known line', () => {
  // This is the actual bug: at kickoff Pinnacle stops quoting, the board
  // rebuilds, and the number used to disappear.
  const before = game('Ball State', 'Ohio State', '2026-09-05T16:30:00Z',
                      { spread: 50.5, total: 53.5, fav: 'home' });
  const state = { lines: {}, sent: {} };
  applyLineCache(state, 'NCAAF', [before], new Date('2026-09-05T16:00:00Z'));

  const after = game('Ball State', 'Ohio State', '2026-09-05T16:30:00Z', { state: 'in' });
  applyLineCache(state, 'NCAAF', [after], new Date('2026-09-05T17:00:00Z'));

  assert.equal(after.spread, 50.5);
  assert.equal(after.total, 53.5);
  assert.equal(after.fav, 'home');
  assert.equal(after.stale, true, 'restored lines are flagged so the board can mark them');
});

test('a cached line past its TTL is not reused', () => {
  const g1 = game('Ball State', 'Ohio State', '2026-09-05T16:30:00Z',
                  { spread: 50.5, total: 53.5, fav: 'home' });
  const state = { lines: {}, sent: {} };
  applyLineCache(state, 'NCAAF', [g1], new Date('2026-09-05T16:00:00Z'));

  const g2 = game('Ball State', 'Ohio State', '2026-09-05T16:30:00Z');
  applyLineCache(state, 'NCAAF', [g2], new Date('2026-09-08T16:00:00Z')); // 3 days later
  assert.equal(g2.spread, null);
});

test('a fresh live line overwrites a stale cached one', () => {
  const state = { lines: {}, sent: {} };
  const g1 = game('A', 'B', '2026-09-05T16:30:00Z', { spread: 7, total: 44, fav: 'home' });
  applyLineCache(state, 'NCAAF', [g1], new Date('2026-09-05T15:00:00Z'));
  const g2 = game('A', 'B', '2026-09-05T16:30:00Z', { spread: 9, total: 45, fav: 'home' });
  applyLineCache(state, 'NCAAF', [g2], new Date('2026-09-05T16:00:00Z'));
  assert.equal(state.lines[lineKey('NCAAF', g2)].spread, 9);
  assert.equal(g2.stale, undefined);
});

test('state survives a save/load round trip and prunes expired entries', () => {
  const dir = mkdtempSync(join(tmpdir(), 'spreads-'));
  const path = join(dir, 'state.json');
  try {
    const state = { lines: {}, sent: {} };
    const fresh = game('A', 'B', '2026-09-05T16:30:00Z', { spread: 3, total: 40, fav: 'home' });
    applyLineCache(state, 'NCAAF', [fresh], new Date('2026-09-05T16:00:00Z'));
    state.lines.ancient = { spread: 1, total: 2, fav: 'home', at: 0 };
    state.sent['20260905_1'] = true;
    state.sent['20260101_1'] = true;

    saveState(path, state, new Date('2026-09-05T16:00:00Z'));
    const back = loadState(path);
    assert.equal(back.lines.ancient, undefined, 'expired line pruned');
    assert.equal(Object.keys(back.lines).length, 1);
    assert.equal(back.sent['20260905_1'], true);
    assert.equal(back.sent['20260101_1'], undefined, 'stale send markers pruned');
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('a missing state file loads as empty rather than throwing', () => {
  const s = loadState('/nonexistent/path/state.json');
  assert.deepEqual(s, { lines: {}, sent: {} });
});
