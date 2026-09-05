// ESPN is the slate authority: which games exist, who is playing, when they kick.
import { ESPN_URLS } from '../config.js';
import { etDateStr } from '../tz.js';
import { getJson } from './http.js';

function altNames(t) {
  return [t.shortDisplayName, t.displayName, t.location, t.name].filter(Boolean);
}

export function parseEspnSlate(data, dateStr) {
  const games = [];
  for (const e of data.events || []) {
    const comp = e.competitions && e.competitions[0];
    if (!comp) continue;
    const kick = new Date(e.date);
    if (etDateStr(kick) !== dateStr) continue;
    const teams = {};
    for (const c of comp.competitors || []) teams[c.homeAway] = c;
    if (!teams.home || !teams.away) continue;
    const state = ((comp.status || {}).type || {}).state || 'pre';
    games.push({
      away: teams.away.team.shortDisplayName,
      home: teams.home.team.shortDisplayName,
      awayNames: altNames(teams.away.team),
      homeNames: altNames(teams.home.team),
      awayScore: teams.away.score ?? null,
      homeScore: teams.home.score ?? null,
      kick,
      state
    });
  }
  games.sort((a, b) => a.kick - b.kick);
  return games;
}

export async function fetchEspnSlate(league, dateStr) {
  const data = await getJson(ESPN_URLS[league].replace('{d}', dateStr));
  return parseEspnSlate(data, dateStr);
}
