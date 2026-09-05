// Renders the board as self-contained HTML: one block per league, two rows per
// game, away on top. Spread sits on the favourite's row; a -1 prints on both.
import { rotationNumbers } from './lines.js';
import { etTime, etStamp } from './tz.js';

function esc(s) {
  return String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function rows(games) {
  const rots = rotationNumbers(games.length);
  const out = [];
  games.forEach((g, i) => {
    const sp = g.spread !== null ? `-${g.spread}` : '';
    const pk = g.spread === 1; // -1 either way plays as a pick'em: -1 on both sides
    const [t0, t1] = rots.team[i];
    const [u0, u1] = rots.ou[i];
    const cls = [g.state !== 'pre' ? 'started' : '', g.stale ? 'stale' : ''].filter(Boolean).join(' ');
    out.push(`<tr class="top ${cls}">
      <td>${t0}</td><td class="team">${esc(g.away)}</td>
      <td>${(g.fav === 'away' || pk) ? sp : ''}</td><td></td>
      <td>${u0}</td><td></td></tr>`);
    out.push(`<tr class="bot ${cls}">
      <td>${t1}</td><td class="team">${esc(g.home)}</td>
      <td>${(g.fav === 'home' || pk) ? sp : ''}</td><td>${etTime(g.kick)}</td>
      <td>${u1}</td><td>${g.total !== null ? g.total : ''}</td></tr>`);
  });
  return out.join('\n');
}

export function renderBoardHtml(blocks, now = new Date()) {
  const tables = blocks.map(b => `
    <table>
      <thead>
        <tr class="label"><th colspan="6">${esc(b.label)}</th></tr>
        <tr class="sub"><th>Rot #</th><th>Team</th><th>Spread</th><th>Time</th><th>O/U #</th><th>Total</th></tr>
      </thead>
      <tbody>${rows(b.games)}</tbody>
    </table>`).join('\n');

  return `<!doctype html><html><head><meta charset="utf-8"><title>SPREADS Board</title>
<style>
  body { font-family: Arial, Helvetica, sans-serif; font-weight: bold; color: #000;
         font-size: 10px; margin: 12px; background: #fff; }
  .stamp { font-size: 9px; margin-bottom: 8px; }
  .wrap { display: flex; gap: 20px; align-items: flex-start; flex-wrap: wrap; }
  table { border-collapse: collapse; }
  th, td { text-align: center; padding: 2px 4px; border-left: 1px solid #999; border-right: 1px solid #999; }
  td.team { text-align: left; min-width: 150px; }
  tr.label th { font-size: 12px; background: #d9d9d9; height: 28px; border: 1px solid #333; }
  tr.sub th { font-size: 9px; background: #ededed; }
  tr.top td { border-top: 2px solid #333; }
  tr.bot td { border-bottom: 2px solid #333; }
  tr.started td { color: #888; }
  tr.stale td { font-style: italic; }
  .legend { font-size: 8px; font-weight: normal; color: #666; margin-top: 10px; }
</style></head><body>
<div class="stamp">Updated ${esc(etStamp(now))} ET</div>
<div class="wrap">${tables}</div>
<div class="legend">Grey = in progress or final. Italic = last known line (live sources no longer quoting).</div>
</body></html>`;
}

/** Plain-text summary of the games triggering this send. */
export function summarizeGames(items) {
  return items.map(({ g }) => {
    const fav = g.fav === 'home' ? g.home : g.away;
    const line = g.spread !== null ? `  (${fav} -${g.spread}, O/U ${g.total})` : '';
    return `${etTime(g.kick)}  ${g.away} @ ${g.home}${line}`;
  }).join('\n');
}
