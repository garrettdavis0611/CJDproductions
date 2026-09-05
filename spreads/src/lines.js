// Odds math and slate/line matching. Faithful to the Apps Script rules, with
// the pick'em bug fixed: a spread of 0 is a real line, not a missing one.

const KEYS = new Set([3, 7, 9, 14, 21, 28]);

/** Half-point lines snap to adjacent key numbers; everything else keeps the .5. */
export function roundKey(x) {
  if (x === null || x === undefined) return null;
  const sign = x < 0 ? -1 : 1;
  const ax = Math.abs(x);
  if (ax % 1 === 0.5) {
    if (KEYS.has(ax + 0.5)) return sign * (ax + 0.5);
    if (KEYS.has(ax - 0.5)) return sign * (ax - 0.5);
  }
  return x;
}

export function impliedProb(american) {
  if (american === null || american === undefined) return null;
  return american < 0 ? -american / (-american + 100) : 100 / (american + 100);
}

/** Devigged probability of side A given both sides' American prices. */
export function devig(priceA, priceB) {
  const a = impliedProb(priceA);
  const b = impliedProb(priceB);
  if (a === null || b === null) return null;
  return a / (a + b);
}

// Shaded juice moves the number half a point. dv >= 0.535 (~ -118 vs the other
// side) means the market leans past this number toward that side.
export function pinnacleFairSpread(ln) {
  if (ln.homeSpread === null || ln.homeSpread === undefined) return null;
  let line = ln.homeSpread;
  const dv = devig(ln.homePrice, ln.awayPrice);
  const dist = dv === null ? 0.5 : Math.abs(dv - 0.5);
  if (dv !== null) {
    if (dv >= 0.535) line -= 0.5;
    else if (dv <= 0.465) line += 0.5;
  }
  return { line, dist };
}

export function pinnacleFairTotal(ln) {
  if (ln.total === null || ln.total === undefined) return null;
  let t = ln.total;
  const dv = devig(ln.overPrice, ln.underPrice);
  const dist = dv === null ? 0.5 : Math.abs(dv - 0.5);
  if (dv !== null) {
    if (dv >= 0.535) t += 0.5;
    else if (dv <= 0.465) t -= 0.5;
  }
  return { line: t, dist };
}

// Agree -> use it. Disagree -> whichever source priced closest to a balanced
// -110/-110 market wins.
export function decide(pinn, kal) {
  if (pinn && !kal) return pinn.line;
  if (!pinn && kal) return kal.line;
  if (!pinn && !kal) return null;
  if (Math.abs(pinn.line - kal.line) < 1) return pinn.line;
  return kal.dist < pinn.dist ? kal.line : pinn.line;
}

/** Sorensen-Dice similarity on character bigrams. */
export function dice(a, b) {
  a = String(a).toLowerCase();
  b = String(b).toLowerCase();
  if (a === b) return 1;
  if (a.length < 2 || b.length < 2) return 0;
  const counts = new Map();
  for (let i = 0; i < a.length - 1; i++) {
    const bg = a.substr(i, 2);
    counts.set(bg, (counts.get(bg) || 0) + 1);
  }
  let hits = 0;
  for (let j = 0; j < b.length - 1; j++) {
    const bg = b.substr(j, 2);
    if (counts.get(bg) > 0) { counts.set(bg, counts.get(bg) - 1); hits++; }
  }
  return (2 * hits) / (a.length + b.length - 2);
}

export function nameScore(name, espnNames) {
  let best = 0;
  for (const n of espnNames) best = Math.max(best, dice(name, n));
  return best;
}

/**
 * Attach spread/total/fav to each ESPN game from the Pinnacle and Kalshi feeds.
 * Mutates `espnGames`, matching the original's in-place behaviour.
 */
export function matchLines(espnGames, pinLines, kalshi) {
  // Assign each Kalshi event to at most one game. Two-team events must match
  // both sides; single-team events need a very strong match.
  const assign = {};
  for (const ev of Object.keys(kalshi.spread)) {
    const k = kalshi.spread[ev];
    const teams = k.teams && k.teams.length ? k.teams : (k.team ? [k.team] : []);
    if (!teams.length) continue;
    let bestIdx = -1;
    let evScore = 0;
    espnGames.forEach((g, idx) => {
      let s;
      if (teams.length >= 2) {
        const p1 = Math.min(nameScore(teams[0], g.homeNames), nameScore(teams[1], g.awayNames));
        const p2 = Math.min(nameScore(teams[0], g.awayNames), nameScore(teams[1], g.homeNames));
        s = Math.max(p1, p2);
        if (s < 0.6) s = 0;
      } else {
        s = Math.max(nameScore(teams[0], g.homeNames), nameScore(teams[0], g.awayNames));
        if (s < 0.75) s = 0;
      }
      if (s > evScore) { evScore = s; bestIdx = idx; }
    });
    if (bestIdx > -1 && evScore > 0 && (!assign[bestIdx] || evScore > assign[bestIdx].score)) {
      assign[bestIdx] = { ev, score: evScore };
    }
  }

  espnGames.forEach((g, idx) => {
    let best = null;
    let bestScore = 0;
    for (const ln of pinLines) {
      const dt = Math.abs(ln.kick.getTime() - g.kick.getTime());
      // Delayed kicks: ESPN and Pinnacle can disagree on time.
      if (dt > 75 * 60 * 1000) continue;
      const s = Math.max(nameScore(ln.away, g.awayNames), nameScore(ln.home, g.homeNames)) +
                (dt === 0 ? 0.05 : 0);
      if (s > bestScore) { best = ln; bestScore = s; }
    }
    g.spread = null;
    g.total = null;
    g.fav = null;
    if (!best || bestScore < 0.5) return;

    let kalSpread = null;
    let kalTotal = null;
    if (assign[idx]) {
      const ev = assign[idx].ev;
      const k = kalshi.spread[ev];
      const sHome = nameScore(k.team, g.homeNames);
      const sAway = nameScore(k.team, g.awayNames);
      const homeLine = sHome >= sAway ? -k.line : k.line;
      kalSpread = { line: homeLine, dist: k.dist };
      if (kalshi.total[ev]) kalTotal = { line: kalshi.total[ev].line, dist: kalshi.total[ev].dist };
    }

    const spreadLine = decide(pinnacleFairSpread(best), kalSpread);
    if (spreadLine !== null && spreadLine !== undefined) {
      g.spread = roundKey(Math.abs(spreadLine));
      g.fav = spreadLine < 0 ? 'home' : 'away';
    }
    const totalLine = decide(pinnacleFairTotal(best), kalTotal);
    if (totalLine !== null && totalLine !== undefined) g.total = roundKey(totalLine);
  });
}

/** Sequential numbering: teams 1..2N, O/U numbers continue right after. */
export function rotationNumbers(nGames) {
  const team = [];
  const ou = [];
  let t = 1;
  let u = nGames * 2 + 1;
  for (let i = 0; i < nGames; i++) {
    team.push([t, t + 1]);
    ou.push([u, u + 1]);
    t += 2;
    u += 2;
  }
  return { team, ou };
}
