// CJD → Northeastern sheet sync (Apps Script, lives on the master sheet)
// When a Northeastern row changes on the master "Schedule" tab, mirror the crew
// into the matching game's "Videoboard Producer" cell on NU's marketing grid.
// One-time: run setup() once and approve the authorization prompt.

const NU_SHEET_ID = "1-Cq941_dfPA8j01j9gN0Jm1yOEpXfEeZMiOoyIG3qQo";
const MONTH_NAMES = ["January","February","March","April","May","June","July","August","September","October","November","December"];

function setup() {
  ScriptApp.getProjectTriggers().forEach(function (t) { ScriptApp.deleteTrigger(t); });
  ScriptApp.newTrigger("onMasterEdit").forSpreadsheet(SpreadsheetApp.getActive()).onEdit().create();
  console.log("NU sheet opened OK: " + SpreadsheetApp.openById(NU_SHEET_ID).getName());
  syncAll();
}

function onMasterEdit(e) {
  try {
    const sh = e.range.getSheet();
    if (sh.getName() !== "Schedule") return;
    const top = Math.max(e.range.getRow(), 2);
    const bottom = Math.min(e.range.getRow() + e.range.getNumRows() - 1, sh.getLastRow());
    for (let row = top; row <= bottom; row++) {
      const vals = sh.getRange(row, 1, 1, 13).getValues()[0];
      if (/^Northeastern/i.test(String(vals[2]))) syncRow(vals);
    }
  } catch (err) {
    console.error("NU sync failed: " + err);
  }
}

function syncRow(vals) {
  const m = String(vals[0]).match(/^(\d{4})-(\d{1,2})-(\d{1,2})/);
  if (!m) return;
  const nuDate = MONTH_NAMES[+m[2] - 1] + " " + (+m[3]);
  const team = String(vals[2]);
  const crew = vals.slice(7, 12).map(function (s) { return String(s).trim(); }).filter(Boolean).join(", ");

  const nu = SpreadsheetApp.openById(NU_SHEET_ID);
  const tabs = nu.getSheets();
  for (let i = 0; i < tabs.length; i++) {
    const info = tabInfo(tabs[i], team);
    if (!info) continue;
    const data = tabs[i].getRange(1, 1, tabs[i].getLastRow(), tabs[i].getLastColumn()).getValues();
    for (let r = info.headerRow; r < data.length; r++) {
      if (normDate(data[r][info.dateCol]) !== nuDate) continue;
      if (info.teamCol >= 0 && !teamMatches(team, String(data[r][info.teamCol]))) continue;
      tabs[i].getRange(r + 1, info.prodCol + 1).setValue(crew);
      console.log("WROTE '" + crew + "' -> " + tabs[i].getName() + " row " + (r + 1) + " (" + nuDate + " " + team + ")");
      return;
    }
  }
  console.log("NO MATCH: " + team + " " + nuDate);
}

// Identify each NU tab by its headers (no reliance on tab names):
// hockey tab has a "Team" column; volleyball has "Post Set 1";
// men's bball has "1st Half - Media 16"; women's bball has "1st Quarter - Under 5".
function tabInfo(tab, team) {
  let scan;
  try {
    if (tab.getLastRow() < 1 || tab.getLastColumn() < 1) return null;
    scan = tab.getRange(1, 1, Math.min(6, tab.getLastRow()), tab.getLastColumn()).getValues();
  } catch (e) {
    return null; // tab is protected/hidden from this account - skip it
  }
  for (let i = 0; i < scan.length; i++) {
    const hdr = scan[i].map(String);
    const prodCol = hdr.findIndex(function (h) { return /videoboard/i.test(h) && /producer/i.test(h); });
    const dateCol = hdr.findIndex(function (h) { return /^date$/i.test(h.trim()); });
    if (prodCol === -1 || dateCol === -1) continue;
    const teamCol = hdr.findIndex(function (h) { return /^team$/i.test(h.trim()); });
    const joined = hdr.join(" | ");
    const nm = tab.getName();
    const isHockey = teamCol >= 0;
    const isMBB = /1st half/i.test(joined);
    const isWBB = /1st quarter/i.test(joined);
    const ok =
      (/Hockey/.test(team) && (isHockey || /hockey/i.test(nm))) ||
      (/Men's Basketball/.test(team) && (isMBB || (/men'?s basketball/i.test(nm) && !/women/i.test(nm)))) ||
      (/Women's Basketball/.test(team) && (isWBB || /women'?s basketball/i.test(nm))) ||
      (/Volleyball/.test(team) && (/volleyball/i.test(nm) || (!isHockey && !isMBB && !isWBB)));
    return ok ? { headerRow: i + 1, prodCol: prodCol, dateCol: dateCol, teamCol: teamCol } : null;
  }
  return null;
}

function teamMatches(masterTeam, nuTeamCell) {
  return /Men's/.test(masterTeam)
    ? /men/i.test(nuTeamCell) && !/women/i.test(nuTeamCell)
    : /women/i.test(nuTeamCell);
}

function normDate(v) {
  if (v instanceof Date) return MONTH_NAMES[v.getMonth()] + " " + v.getDate();
  const mm = String(v).trim().match(/^([A-Za-z]+)\s+(\d{1,2})/);
  return mm ? mm[1] + " " + (+mm[2]) : String(v).trim();
}

// One-shot backfill: push every Northeastern row's crew into NU's sheet.
function syncAll() {
  const sh = SpreadsheetApp.getActive().getSheetByName("Schedule");
  const vals = sh.getRange(2, 1, sh.getLastRow() - 1, 13).getValues();
  let n = 0;
  vals.forEach(function (v) {
    if (/^Northeastern/i.test(String(v[2]))) { syncRow(v); n++; }
  });
  console.log("synced " + n + " Northeastern rows");
}

function testOpen() {
  console.log(SpreadsheetApp.openById(NU_SHEET_ID).getName());
}
