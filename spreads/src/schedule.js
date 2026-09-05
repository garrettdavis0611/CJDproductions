// When to email. Pure logic, deliberately free of PDF/SMTP imports so it can be
// exercised without those dependencies present.
import { KICKOFF_WINDOW_MIN } from './config.js';
import { etDateStr } from './tz.js';

/**
 * Games kicking inside the window that have not triggered a send yet.
 * The dedupe key is the kickoff instant, so a whole wave emails once.
 */
export function gamesKickingSoon(blocks, state, now, windowMin = KICKOFF_WINDOW_MIN) {
  const dateStr = etDateStr(now);
  const soon = [];
  for (const b of blocks) {
    for (const g of b.games) {
      const dt = g.kick.getTime() - now.getTime();
      if (dt > 0 && dt <= windowMin * 60 * 1000) {
        const key = `${dateStr}_${g.kick.getTime()}`;
        if (!state.sent[key]) soon.push({ g, key });
      }
    }
  }
  return soon;
}
