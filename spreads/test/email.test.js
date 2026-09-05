import test from 'node:test';
import assert from 'node:assert/strict';
import { gamesKickingSoon } from '../src/schedule.js';

function block(games) {
  return [{ label: 'NCAAF 9/5', games }];
}
function game(away, home, kick) {
  return { away, home, kick: new Date(kick), state: 'pre', spread: 7, total: 50, fav: 'home' };
}

const NOW = new Date('2026-09-05T16:00:00Z');

test('a game kicking inside the window triggers a send', () => {
  const b = block([game('A', 'B', '2026-09-05T16:15:00Z')]); // 15 min out
  const soon = gamesKickingSoon(b, { lines: {}, sent: {} }, NOW);
  assert.equal(soon.length, 1);
});

test('a game further out than the window does not', () => {
  const b = block([game('A', 'B', '2026-09-05T17:00:00Z')]); // 60 min out
  assert.equal(gamesKickingSoon(b, { lines: {}, sent: {} }, NOW).length, 0);
});

test('a game that already kicked does not', () => {
  const b = block([game('A', 'B', '2026-09-05T15:45:00Z')]);
  assert.equal(gamesKickingSoon(b, { lines: {}, sent: {} }, NOW).length, 0);
});

test('each kickoff wave emails once, not every run', () => {
  const b = block([game('A', 'B', '2026-09-05T16:15:00Z')]);
  const state = { lines: {}, sent: {} };
  const first = gamesKickingSoon(b, state, NOW);
  assert.equal(first.length, 1);
  for (const s of first) state.sent[s.key] = true;
  // next run, five minutes later, same game still inside the window
  const second = gamesKickingSoon(b, state, new Date('2026-09-05T16:05:00Z'));
  assert.equal(second.length, 0, 'already-sent wave must not resend');
});

test('games sharing a kickoff time collapse into one wave', () => {
  const b = block([
    game('A', 'B', '2026-09-05T16:15:00Z'),
    game('C', 'D', '2026-09-05T16:15:00Z')
  ]);
  const soon = gamesKickingSoon(b, { lines: {}, sent: {} }, NOW);
  assert.equal(soon.length, 2);
  assert.equal(new Set(soon.map(s => s.key)).size, 1, 'one dedupe key for the wave');
});
