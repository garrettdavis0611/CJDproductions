// CJD Production Schedule — iOS home-screen widget for Scriptable (scriptable.app)
//
// SETUP (per person):
//   1. Install the free "Scriptable" app from the App Store.
//   2. In Scriptable, tap "+", paste this whole file, name it "CJD Schedule".
//   3. Long-press the home screen → add a Scriptable widget (small/medium/large).
//   4. Long-press the widget → Edit Widget → Script: "CJD Schedule",
//      Parameter: your name (e.g. Garrett) to see only YOUR shifts.
//      Leave Parameter empty to see all games.
//   Tapping the widget opens the full schedule site.

const SHEET_ID = "1f87gUR8wuRIukrP7muhQxP0tVa3Ja2RR9eg7xZoSZjk";
const CSV_URL = "https://docs.google.com/spreadsheets/d/" + SHEET_ID + "/gviz/tq?tqx=out:csv";
const SITE_URL = "https://garrettdavis0611.github.io/CJDproductions/";

const TEAM_COLORS = [
  { re: /legacy/i,                        hex: "#00843D" },
  { re: /northeastern|husk/i,             hex: "#C8102E" },
  { re: /rhode\s*island|rifc/i,           hex: "#1D4ED8" },
  { re: /boston\s*college|\bbc\b|eagle/i, hex: "#B7962B" }
];
function teamColor(team) {
  for (const t of TEAM_COLORS) if (t.re.test(team)) return t.hex;
  return "#6b7280";
}

// ── data ──────────────────────────────────────────────────────────────────────
function parseCSV(text) {
  const rows = []; let row = [], cur = "", inQ = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inQ) {
      if (c === '"') { if (text[i + 1] === '"') { cur += '"'; i++; } else inQ = false; }
      else cur += c;
    } else if (c === '"') inQ = true;
    else if (c === ",") { row.push(cur); cur = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(cur); cur = "";
      if (row.some(x => x.trim() !== "")) rows.push(row);
      row = [];
    } else cur += c;
  }
  row.push(cur);
  if (row.some(x => x.trim() !== "")) rows.push(row);
  return rows;
}

function toRecords(rows) {
  if (!rows.length) return [];
  const head = rows[0].map(h => h.toLowerCase().replace(/[^a-z0-9]/g, ""));
  const idx = (...names) => { for (const n of names) { const j = head.indexOf(n); if (j !== -1) return j; } return -1; };
  const iDate = idx("date"), iTeam = idx("team"), iEv = idx("eventjob", "event"),
        iLoc = idx("location", "venue"), iCall = idx("calltime"), iStart = idx("starttime"),
        iWho = idx("whosworking", "crew"),
        iCrew = [idx("crew1"), idx("crew2"), idx("crew3"), idx("crew4"), idx("crew5")];
  const out = [];
  for (let r = 1; r < rows.length; r++) {
    const g = j => (j >= 0 && rows[r][j] != null ? String(rows[r][j]).trim() : "");
    const m = g(iDate).match(/^(\d{4})-(\d{1,2})-(\d{1,2})/);
    const crew = iCrew.map(g).filter(Boolean);
    const who = g(iWho);
    if (who) crew.push(...who.split(/[,;\/]+/).map(x => x.trim()).filter(Boolean));
    out.push({
      d: m ? new Date(+m[1], +m[2] - 1, +m[3]) : null,
      date: g(iDate), team: g(iTeam), event: g(iEv), loc: g(iLoc),
      call: g(iCall), start: g(iStart), crew
    });
  }
  return out;
}

function pickShifts(recs, person, now, n) {
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  let list = recs.filter(r => r.d && r.d >= today && (r.event || r.crew.length));
  if (person) {
    const p = person.toLowerCase();
    list = list.filter(r => r.crew.some(c => c.toLowerCase() === p));
  }
  list.sort((a, b) => a.d - b.d);
  return list.slice(0, n);
}

const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function dayLabel(d, now) {
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const diff = Math.round((d - today) / 86400000);
  if (diff === 0) return "Today";
  if (diff === 1) return "Tomorrow";
  return DAYS[d.getDay()] + " " + MONS[d.getMonth()] + " " + d.getDate();
}
function shortDay(d, now) {
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const diff = Math.round((d - today) / 86400000);
  if (diff === 0) return "Today";
  if (diff === 1) return "Tmrw";
  return DAYS[d.getDay()] + " " + (d.getMonth() + 1) + "/" + d.getDate();
}

// ── widget ────────────────────────────────────────────────────────────────────
async function main() {
  const person = (args.widgetParameter || "").trim();
  let recs = [], failed = false;
  try {
    const req = new Request(CSV_URL);
    req.timeoutInterval = 15;
    const text = await req.loadString();
    if (/<html/i.test(text)) throw new Error("not csv");
    recs = toRecords(parseCSV(text));
  } catch (e) { failed = true; }

  const fam = config.widgetFamily || "medium";
  const n = fam === "small" ? 1 : fam === "large" ? 7 : 3;
  const now = new Date();
  const shifts = pickShifts(recs, person, now, n);

  const w = new ListWidget();
  w.url = SITE_URL;
  w.backgroundColor = Color.dynamic(new Color("#ffffff"), new Color("#131a28"));
  w.setPadding(14, 14, 12, 14);
  w.refreshAfterDate = new Date(Date.now() + 30 * 60 * 1000);

  const ink = Color.dynamic(new Color("#101420"), new Color("#f2f4f8"));
  const soft = Color.dynamic(new Color("#66708a"), new Color("#93a0b9"));
  const accent = new Color("#3b82f6");

  const head = w.addText(person ? person.toUpperCase() + " · CJD" : "CJD SCHEDULE");
  head.font = Font.boldSystemFont(10);
  head.textColor = accent;
  w.addSpacer(6);

  if (failed) {
    const t = w.addText("Couldn't load the schedule.");
    t.font = Font.mediumSystemFont(12); t.textColor = soft;
  } else if (!shifts.length) {
    const t = w.addText(person ? "No upcoming shifts for " + person + "." : "No upcoming games.");
    t.font = Font.mediumSystemFont(12); t.textColor = soft;
  } else if (fam === "small") {
    const s = shifts[0];
    const d = w.addText(dayLabel(s.d, now));
    d.font = Font.boldSystemFont(16); d.textColor = ink;
    w.addSpacer(2);
    const ev = w.addText(s.event || s.team);
    ev.font = Font.mediumSystemFont(12); ev.textColor = ink; ev.lineLimit = 2;
    w.addSpacer(4);
    const cl = w.addText("Call " + (s.call || "TBD"));
    cl.font = Font.boldSystemFont(14); cl.textColor = accent;
    w.addSpacer(4);
    const cw = w.addText("👤 " + (s.crew.length ? s.crew.join(", ") : "Unassigned"));
    cw.font = Font.mediumSystemFont(10); cw.textColor = soft; cw.lineLimit = 1;
    w.addSpacer(3);
    const tb = w.addText(s.team.toUpperCase());
    tb.font = Font.semiboldSystemFont(9);
    tb.textColor = new Color(teamColor(s.team));
    w.addSpacer();
  } else {
    for (const s of shifts) {
      const row = w.addStack();
      row.centerAlignContent();
      const dot = row.addText("●");
      dot.font = Font.systemFont(9);
      dot.textColor = new Color(teamColor(s.team));
      row.addSpacer(6);
      const dt = row.addText(shortDay(s.d, now));
      dt.font = Font.semiboldSystemFont(12); dt.textColor = soft;
      row.addSpacer(8);
      const ev = row.addText(s.event || s.team);
      ev.font = Font.mediumSystemFont(12); ev.textColor = ink; ev.lineLimit = 1;
      row.addSpacer();
      const cl = row.addText(s.call || "TBD");
      cl.font = Font.boldSystemFont(12); cl.textColor = accent;
      const sub = w.addStack();
      sub.addSpacer(15);
      const cw = sub.addText((s.crew.length ? s.crew.join(", ") : "Unassigned") + " · " + s.team);
      cw.font = Font.mediumSystemFont(10); cw.textColor = soft; cw.lineLimit = 1;
      w.addSpacer(fam === "large" ? 7 : 4);
    }
    w.addSpacer();
  }

  if (config.runsInWidget) Script.setWidget(w);
  else if (fam === "small") await w.presentSmall();
  else if (fam === "large") await w.presentLarge();
  else await w.presentMedium();
  Script.complete();
}

// Scriptable wraps scripts in an async context, so top-level await is valid there.
// (Node-based tests stub `config` off and never reach this line.)
if (typeof config !== "undefined") { await main(); }
