// Entry point. Same cadence and same email rule as the Apps Script it replaces:
// rebuild the board, and when games kick within the next ~20 minutes, email the
// PDF once per kickoff wave.
import { mkdirSync, writeFileSync } from 'node:fs';
import { LEAGUES, KICKOFF_WINDOW_MIN, MAIL_TO } from './config.js';
import { etDateStr, etMonthDay, etTime } from './tz.js';
import { fetchEspnSlate } from './sources/espn.js';
import { fetchPinnacleLines } from './sources/pinnacle.js';
import { fetchKalshiFair } from './sources/kalshi.js';
import { matchLines } from './lines.js';
import { loadState, saveState, applyLineCache } from './state.js';
import { renderBoardHtml, summarizeGames } from './board.js';
import { gamesKickingSoon } from './schedule.js';
import { htmlToPdf } from './pdf.js';
import { sendBoard } from './mailer.js';

const STATE_PATH = process.env.STATE_PATH || 'state.json';
const OUT_DIR = process.env.OUT_DIR || 'out';

export async function buildBlocks(now, state) {
  const dateStr = etDateStr(now);
  const mmdd = etMonthDay(now);
  const blocks = [];
  const slateErrors = [];

  for (const lg of LEAGUES) {
    let slate;
    try {
      slate = await fetchEspnSlate(lg, dateStr);
    } catch (err) {
      console.error(`ESPN ${lg} failed: ${err.message}`);
      slateErrors.push(`${lg}: ${err.message}`);
      continue;
    }
    if (!slate.length) continue;

    let pin = [];
    try {
      pin = await fetchPinnacleLines(lg);
    } catch (err) {
      console.error(`Pinnacle ${lg} failed: ${err.message}`);
    }

    let kalshi = { spread: {}, total: {} };
    try {
      kalshi = await fetchKalshiFair(lg, now);
    } catch (err) {
      console.error(`Kalshi ${lg} failed: ${err.message}`);
    }

    matchLines(slate, pin, kalshi);
    applyLineCache(state, lg, slate, now);

    const priced = slate.filter(g => g.spread !== null).length;
    console.log(`${lg}: ${slate.length} games, ${priced} priced ` +
                `(${slate.filter(g => g.stale).length} from cache)`);
    blocks.push({ label: `${lg} ${mmdd}`, games: slate });
  }
  return { blocks, slateErrors };
}

async function main() {
  const dryRun = process.argv.includes('--dry-run');
  const force = process.argv.includes('--force-email');
  const now = new Date();
  const dateStr = etDateStr(now);

  const state = loadState(STATE_PATH);
  const { blocks, slateErrors } = await buildBlocks(now, state);

  if (!blocks.length) {
    saveState(STATE_PATH, state, now);
    // An empty slate is normal on an off day. An empty slate *because every
    // source errored* is a broken board, and must not pass silently — that is
    // how the old version failed unnoticed.
    if (slateErrors.length) {
      throw new Error(`Could not reach the slate source for any league:\n  ${slateErrors.join('\n  ')}`);
    }
    console.log('No games on the slate today — nothing to build.');
    return;
  }

  const totalGames = blocks.reduce((n, b) => n + b.games.length, 0);
  const pricedGames = blocks.reduce((n, b) => n + b.games.filter(g => g.spread !== null).length, 0);
  if (pricedGames === 0) {
    console.warn(`WARNING: ${totalGames} games on the board and not one has a line. ` +
                 'Check the Pinnacle key and the Kalshi series tickers.');
  }

  const html = renderBoardHtml(blocks, now);
  mkdirSync(OUT_DIR, { recursive: true });
  writeFileSync(`${OUT_DIR}/board.html`, html);

  const soon = gamesKickingSoon(blocks, state, now);
  const shouldSend = soon.length > 0 || force;

  if (!shouldSend) {
    console.log('No kickoffs in the next ' + KICKOFF_WINDOW_MIN + ' minutes — board rebuilt, no email.');
    saveState(STATE_PATH, state, now);
    return;
  }

  const pdf = await htmlToPdf(html);
  const filename = `SPREADS_Board_${dateStr}.pdf`;
  writeFileSync(`${OUT_DIR}/${filename}`, pdf);

  const first = soon.length ? soon[0].g.kick : now;
  const subject = `SPREADS Board - Kickoff ${etTime(first)}`;
  const text = soon.length
    ? `Games kicking soon:\n\n${summarizeGames(soon)}`
    : 'Current board.';

  if (dryRun) {
    console.log(`[dry-run] would email ${MAIL_TO}: ${subject}`);
    console.log(text);
  } else {
    await sendBoard({ subject, text, html, pdf, filename });
    console.log(`Emailed ${MAIL_TO}: ${subject}`);
    for (const s of soon) state.sent[s.key] = true;
  }

  saveState(STATE_PATH, state, now);
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch(err => {
    console.error(err);
    process.exit(1);
  });
}
