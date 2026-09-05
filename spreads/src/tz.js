// All date logic runs in America/New_York regardless of where the job executes.
import { TZ } from './config.js';

const MONTHS = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];

function parts(d, opts) {
  const fmt = new Intl.DateTimeFormat('en-US', { timeZone: TZ, ...opts });
  const out = {};
  for (const p of fmt.formatToParts(d)) out[p.type] = p.value;
  return out;
}

/** 'yyyyMMdd' in ET — the key ESPN's scoreboard endpoint takes. */
export function etDateStr(d) {
  const p = parts(d, { year: 'numeric', month: '2-digit', day: '2-digit' });
  return `${p.year}${p.month}${p.day}`;
}

/** 'M/d' in ET — the tab / block label. */
export function etMonthDay(d) {
  const p = parts(d, { month: 'numeric', day: 'numeric' });
  return `${p.month}/${p.day}`;
}

/** 'h:mm AM' in ET — kickoff time as shown on the board. */
export function etTime(d) {
  const p = parts(d, { hour: 'numeric', minute: '2-digit', hour12: true });
  return `${p.hour}:${p.minute} ${p.dayPeriod.toUpperCase()}`;
}

/** 'M/d h:mm AM' in ET — the "Updated" stamp. */
export function etStamp(d) {
  return `${etMonthDay(d)} ${etTime(d)}`;
}

/** 'yyMMMdd' uppercase in ET — the date fragment inside Kalshi tickers. */
export function etDateCode(d) {
  const p = parts(d, { year: '2-digit', month: 'numeric', day: '2-digit' });
  return `${p.year}${MONTHS[Number(p.month) - 1]}${p.day}`;
}
