// Configuration for the SPREADS board. Values mirror the original Apps Script.
export const TZ = 'America/New_York';

export const PIN_BASE = 'https://guest.api.arcadia.pinnacle.com/0.1';
// Public key lifted from pinnacle.com's own frontend. If lines stop arriving
// league-wide, check this first — Pinnacle rotates it periodically.
export const PIN_KEY = process.env.PINNACLE_KEY || 'CmX2KcMrXuFmNg6YFbmTxE0y9CIrOi0R';
export const PIN_LEAGUES = { NCAAF: 880, NFL: 889 };

export const ESPN_URLS = {
  NCAAF: 'https://site.web.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard?dates={d}&groups=80&limit=200',
  NFL: 'https://site.web.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?dates={d}&limit=100'
};

export const KALSHI_BASE = 'https://api.elections.kalshi.com/trade-api/v2';
export const KALSHI_SERIES = {
  NCAAF: { spread: 'KXNCAAFSPREAD', total: 'KXNCAAFTOTAL' },
  NFL: { spread: 'KXNFLSPREAD', total: 'KXNFLTOTAL' }
};

export const LEAGUES = ['NCAAF', 'NFL'];

// Email a fresh board when games kick within this window (same as the script).
export const KICKOFF_WINDOW_MIN = Number(process.env.KICKOFF_WINDOW_MIN || 20);
// How long a remembered line stays usable once the live sources drop it.
export const LINE_CACHE_TTL_H = Number(process.env.LINE_CACHE_TTL_H || 30);

export const MAIL_TO = process.env.MAIL_TO || 'garrett.davis0611@gmail.com';
export const MAIL_FROM = process.env.MAIL_FROM || process.env.SMTP_USER || MAIL_TO;
