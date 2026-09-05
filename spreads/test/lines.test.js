import test from 'node:test';
import assert from 'node:assert/strict';
import {
  roundKey, impliedProb, devig, pinnacleFairSpread, pinnacleFairTotal,
  decide, dice, matchLines, rotationNumbers
} from '../src/lines.js';

test('roundKey snaps half points onto key numbers', () => {
  assert.equal(roundKey(2.5), 3);
  assert.equal(roundKey(3.5), 3);
  assert.equal(roundKey(6.5), 7);
  assert.equal(roundKey(7.5), 7);
  assert.equal(roundKey(13.5), 14);
  assert.equal(roundKey(27.5), 28);
  assert.equal(roundKey(-2.5), -3);
});

test('roundKey leaves non-key half points and whole numbers alone', () => {
  assert.equal(roundKey(10.5), 10.5);
  assert.equal(roundKey(49.5), 49.5);
  assert.equal(roundKey(24), 24);
  assert.equal(roundKey(null), null);
});

test('impliedProb handles both American price signs', () => {
  assert.equal(impliedProb(-110).toFixed(4), '0.5238');
  assert.equal(impliedProb(100), 0.5);
  assert.equal(impliedProb(-200).toFixed(4), '0.6667');
});

test('devig removes the vig from a balanced market', () => {
  assert.equal(devig(-110, -110), 0.5);
  assert.ok(devig(-130, 110) > 0.5);
});

test('pinnacleFairSpread nudges half a point on shaded juice', () => {
  // balanced -110/-110 leaves the number alone
  assert.equal(pinnacleFairSpread({ homeSpread: -7, homePrice: -110, awayPrice: -110 }).line, -7);
  // home shaded -> true line further toward home
  assert.equal(pinnacleFairSpread({ homeSpread: -7, homePrice: -130, awayPrice: 110 }).line, -7.5);
  // away shaded -> true line back toward away
  assert.equal(pinnacleFairSpread({ homeSpread: -7, homePrice: 110, awayPrice: -130 }).line, -6.5);
});

test('pinnacleFairSpread treats a pick-em as a real line, not a missing one', () => {
  const fair = pinnacleFairSpread({ homeSpread: 0, homePrice: -110, awayPrice: -110 });
  assert.notEqual(fair, null, 'homeSpread of 0 must not be discarded');
  assert.equal(fair.line, 0);
});

test('pinnacleFairSpread still rejects a genuinely absent line', () => {
  assert.equal(pinnacleFairSpread({ homeSpread: null }), null);
  assert.equal(pinnacleFairSpread({}), null);
});

test('pinnacleFairTotal moves the total up when the over is shaded', () => {
  assert.equal(pinnacleFairTotal({ total: 51, overPrice: -110, underPrice: -110 }).line, 51);
  assert.equal(pinnacleFairTotal({ total: 51, overPrice: -130, underPrice: 110 }).line, 51.5);
  assert.equal(pinnacleFairTotal({ total: 51, overPrice: 110, underPrice: -130 }).line, 50.5);
  assert.equal(pinnacleFairTotal({ total: null }), null);
});

test('decide prefers the tighter-priced source when the two disagree', () => {
  assert.equal(decide({ line: -7, dist: 0.02 }, null), -7);
  assert.equal(decide(null, { line: -6, dist: 0.01 }), -6);
  assert.equal(decide(null, null), null);
  // within a point -> Pinnacle wins
  assert.equal(decide({ line: -7, dist: 0.05 }, { line: -7.5, dist: 0.01 }), -7);
  // more than a point apart -> closest to balanced wins
  assert.equal(decide({ line: -7, dist: 0.05 }, { line: -9, dist: 0.01 }), -9);
  assert.equal(decide({ line: -7, dist: 0.01 }, { line: -9, dist: 0.05 }), -7);
});

test('dice similarity scores exact, close and unrelated names', () => {
  assert.equal(dice('Alabama', 'Alabama'), 1);
  assert.ok(dice('Ohio State', 'Ohio St') > 0.7);
  assert.ok(dice('Alabama', 'Auburn') < 0.4);
});

test('rotationNumbers numbers teams then continues with O/U', () => {
  const r = rotationNumbers(3);
  assert.deepEqual(r.team, [[1, 2], [3, 4], [5, 6]]);
  assert.deepEqual(r.ou, [[7, 8], [9, 10], [11, 12]]);
});

function game(away, home, kick) {
  return {
    away, home,
    awayNames: [away], homeNames: [home],
    kick: new Date(kick), state: 'pre'
  };
}

test('matchLines attaches spread, total and favourite to the right side', () => {
  const games = [game('Ball State', 'Ohio State', '2026-09-05T16:30:00Z')];
  const pin = [{
    away: 'Ball State', home: 'Ohio State',
    kick: new Date('2026-09-05T16:30:00Z'),
    homeSpread: -50.5, homePrice: -110, awayPrice: -110,
    total: 53.5, overPrice: -110, underPrice: -110
  }];
  matchLines(games, pin, { spread: {}, total: {} });
  assert.equal(games[0].spread, 50.5);
  assert.equal(games[0].fav, 'home');
  assert.equal(games[0].total, 53.5);
});

test('matchLines leaves a game blank when no line is within the kickoff window', () => {
  const games = [game('Coastal', 'West Virginia', '2026-09-05T16:00:00Z')];
  const pin = [{
    away: 'Coastal', home: 'West Virginia',
    kick: new Date('2026-09-05T20:00:00Z'), // 4h apart, outside the 75-min window
    homeSpread: -3, homePrice: -110, awayPrice: -110,
    total: 47, overPrice: -110, underPrice: -110
  }];
  matchLines(games, pin, { spread: {}, total: {} });
  assert.equal(games[0].spread, null);
  assert.equal(games[0].total, null);
});

test('matchLines flips the Kalshi line to home perspective', () => {
  const games = [game('Clemson', 'LSU', '2026-09-05T23:30:00Z')];
  const pin = [{
    away: 'Clemson', home: 'LSU',
    kick: new Date('2026-09-05T23:30:00Z'),
    // shaded toward home, so Pinnacle's devig distance is wide
    homeSpread: -14, homePrice: -140, awayPrice: 120,
    total: 50.5, overPrice: -110, underPrice: -110
  }];
  // Kalshi says LSU (home) by 10. That is more than a point off Pinnacle's
  // number and priced tighter, so Kalshi should win.
  const kalshi = {
    spread: { EV1: { team: 'LSU', line: 10, dist: 0.01, teams: ['LSU', 'Clemson'] } },
    total: { EV1: { line: 50.5, dist: 0.01 } }
  };
  matchLines(games, pin, kalshi);
  assert.equal(games[0].fav, 'home');
  assert.equal(games[0].spread, 10);
});

test('a balanced Pinnacle market outranks Kalshi even when they disagree', () => {
  const games = [game('Clemson', 'LSU', '2026-09-05T23:30:00Z')];
  const pin = [{
    away: 'Clemson', home: 'LSU',
    kick: new Date('2026-09-05T23:30:00Z'),
    homeSpread: -14, homePrice: -110, awayPrice: -110,
    total: 50.5, overPrice: -110, underPrice: -110
  }];
  const kalshi = {
    spread: { EV1: { team: 'LSU', line: 10, dist: 0.01, teams: ['LSU', 'Clemson'] } },
    total: {}
  };
  matchLines(games, pin, kalshi);
  assert.equal(games[0].spread, 14, 'perfectly balanced -110/-110 has distance 0');
});
