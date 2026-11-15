// Shared helpers for every Primesight demo page. Pages are served by the Primesight server (python -m primesight.server),
// so API calls are same-origin; opened from file:// they fall back to localhost:8787.
const API = location.protocol.startsWith("http") ? "" : "http://localhost:8787";

// Judge passcode for live agent runs on the public deploy: open any page once with ?code=XXXX and it is remembered.
// Without it the deploy still replays recorded calls and sends the free draft briefing for any building.
const PASSCODE = (() => {
  const q = new URLSearchParams(location.search).get("code");
  try { if (q) localStorage.setItem("primesight-code", q); return q || localStorage.getItem("primesight-code") || ""; }
  catch (e) { return q || ""; }
})();

async function api(path, body) {
  const r = await fetch(API + path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(PASSCODE ? { ...body, passcode: PASSCODE } : body) });
  const ct = r.headers.get("content-type") || "";
  if (!r.ok) throw new Error(`${path} -> ${r.status}`);
  return ct.startsWith("application/json") ? r.json() : r.text();  // /trace is application/x-ndjson: keep it text
}

// The kit has stray lowercase and spacing ("3250 wilshire Blvd", city "houston"): capitalise each word, collapse spaces.
const SMALL_WORDS = new Set(["of", "the", "and"]);
const tidy = s => (s || "").trim().split(/\s+/).map((w, i) => i && SMALL_WORDS.has(w) ? w : w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
console.assert(tidy(" 3250  wilshire Blvd") === "3250 Wilshire Blvd" && tidy("1901 Avenue of the Stars") === "1901 Avenue of the Stars" && tidy("3934 FM-1960") === "3934 FM-1960");
const getBuildings = () => api("/buildings").then(list => list.map(b => ({ ...b, address: tidy(b.address), city: tidy(b.city), name: tidy(b.name) })));
const getIncidents = () => api("/incidents");
const CATEGORY_COLORS = { fire: "#ff5a36", medical: "#3fa7ff", hazmat: "#f5c400", rescue: "#b07cff", security: "#ff4d8d", utility: "#2ed3a7" };

function ordinal(n) {
  const s = ["th", "st", "nd", "rd"], v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}

// One dispatch text from the console's choices; the agent parses it like a real CAD narrative.
function dispatchText({ building, floor, incident, details = "", time = "" }) {
  const where = `${building.address}, ${building.city}`;
  const parts = [`${incident.label} on the ${ordinal(floor)} floor, ${where}.`];
  if (details.trim()) { const d = details.trim(); parts.push(d[0].toUpperCase() + d.slice(1).replace(/\.?$/, ".")); }
  if (time) parts.push(`Time of call: ${time}.`);
  return parts.join(" ");
}

// Send a dispatch. mode "draft" skips the LLM (about 4 s); default runs the deepagents agent (30-60 s).
const sendDispatch = (text, mode) => api("/brief", mode ? { text, mode } : { text });

// Calls onRun(run_id) whenever a new run starts on the server (from any console or the phone simulator).
function followLatest(onRun, every = 1000) {
  let last = null;
  const tick = async () => {
    try {
      const { run_id } = await api("/latest");
      if (run_id && run_id !== last) { last = run_id; onRun(run_id); }
    } catch (e) { /* server restarting: keep polling */ }
  };
  tick();
  return setInterval(tick, every);
}

// Streams a run's trace: onStep({t, step, detail, who}) for each new line, onDone(facts) once /run answers.
const SPEED = +new URLSearchParams(location.search).get("speed") || 1;  // ?speed=3 plays replays 3x faster
// Steps play at their recorded t, so a replay (whole trace written at once) animates like the live run did.
function streamTrace(runId, onStep, onDone) {
  let seen = 0, stopped = false;
  const start = performance.now();
  const tick = async () => {
    if (stopped) return;
    try {
      const text = await api(`/trace/${runId}`);
      const lines = text.split("\n").filter(Boolean);
      for (const l of lines.slice(seen)) {
        const d = JSON.parse(l);
        seen++;
        if (d.step === "queue_text" && !d.detail.startsWith("{")) continue;  // older recordings logged queued texts twice
        const wait = d.t * 1000 / SPEED - (performance.now() - start);
        if (wait > 0) await new Promise(r => setTimeout(r, Math.min(wait, 8000)));
        if (stopped) return;
        d.who = d.step.startsWith("helper:") ? "helper" : ["call_received", "address_lookup", "mode", "done", "usage", "graded", "error", "crew_reply"].includes(d.step) ? "system" : "agent";
        try { onStep(d); } catch (e) { console.error("projector step failed", d, e); }  // a page bug must not stall the stream
        if (d.step === "done" || d.step === "error") {
          stopped = true;
          try { onDone && onDone(await api(`/run/${runId}`)); } catch (e) { onDone && onDone(null); }
        }
      }
    } catch (e) { /* trace not written yet (404) or server restarting */ }
    if (!stopped) setTimeout(tick, 600);
  };
  tick();
  return () => { stopped = true; };
}

// The agent's helpers (subagents in primesight/agent.py), by what each one does.
const HELPERS = { "tenant-risk": "Helper checks the tenants for hazards", "notify-writer": "Helper drafts a heads-up for the property manager",
                  "inside-scout": "Helper reads the floor scan", "outside-scout": "Helper checks outside: wind and hydrants" };
// Plain-language name for a trace step, for the projector and the first responder page.
function stepLabel(d) {
  const tool = d.step.replace(/^helper:/, "");
  // Trace args are cut at 200 chars, so JSON.parse often fails: fall back to pulling single fields by regex.
  const detail = (() => { try { return JSON.parse(d.detail); } catch (e) {
    if (!String(d.detail).startsWith("{")) return null;
    const o = {}; for (const m of d.detail.matchAll(/"(\w+)": ("((?:[^"\\]|\\.)*)"?|-?[\d.]+)/g)) o[m[1]] = m[3] ?? +m[2]; return o; } })();
  switch (tool) {
    case "call_received": return ["Call received", d.detail];
    case "address_lookup": return ["Address matched", d.detail];
    case "find_building": return ["Finds the building", detail?.address || d.detail];
    case "read_file": { const p = detail?.file_path || "", skill = p.match(/skills\/(\w+)\//);
      return [skill ? `Opens the ${skill[1]} playbook` : p.startsWith("/buildings/") ? "Reads crew notes on this building" : p.startsWith("/scans/") ? "Reads the floor scan results"
        : p.includes("AGENTS") ? "Reads its standing rules" : p.includes("incidents") ? "Looks up the kind of emergency" : "Reads a file", p || d.detail]; }
    case "ls": case "glob": case "grep": return ["Looks through its files", detail?.path || detail?.pattern || d.detail];
    case "write_todos": return ["Plans the steps", ""];
    case "gather_building": return ["Pulls CoStar + Matterport + Vexcel", detail ? `floor ${detail.floor}, ${detail.incident_type || detail.incident}` : d.detail];
    case "task": return [HELPERS[detail?.subagent_type] || `Hands off to ${detail?.subagent_type || "a helper"}`, detail?.description || ""];
    case "queue_text": return ["Queues a text for building staff", detail ? `${detail.to}: ${detail.text}` : d.detail];
    case "walk_to": return ["Walks the scan graph", detail ? `to nearest ${detail.target}` : d.detail];
    case "outside_now": return ["Checks wind and hydrants now", "Open-Meteo + city hydrant layer"];
    case "add_building_note": return ["Saves a building note", detail?.note || d.detail];
    case "graded": { const [verdict, ...gaps] = String(d.detail).split(" - "), why = gaps.join(" - ");  // "satisfied" or "needs_revision - rule: gap; ..."
      return verdict === "satisfied" ? ["Grader: passed every rule", "At most 8 lines, nothing invented, facts not orders, guesses marked"]
        : verdict === "needs_revision" ? ["Grader: sent back for a rewrite", why]
        : verdict === "max_iterations_reached" ? ["Grader: still not satisfied, sent as written", why] : ["Grader could not finish", d.detail]; }
    case "usage": return ["Cost", d.detail];
    case "done": return ["Briefing sent", d.detail];
    case "mode": return ["Mode", d.detail];
    case "crew_reply": return ["Crew replied", d.detail];
    default: return [tool, d.detail];
  }
}

// Autocomplete list over buildings: filter by any word of name, address or city.
function matchBuildings(list, q) {
  const words = q.toLowerCase().replace(/[^a-z0-9 ]/g, " ").split(/\s+/).filter(Boolean);
  return list.filter(b => words.every(w => `${b.name} ${b.address} ${b.city} ${b.pid}`.toLowerCase().includes(w)));
}

// Floors worth offering first: the SAM 3-scanned floor, then floors with tenants, then the rest.
function floorOptions(b) {
  const top = b.stories || Math.max(...(b.tenant_floors.length ? b.tenant_floors : [1]));
  const out = [];
  for (let f = 1; f <= top; f++) out.push({ floor: f, scanned: f === b.scan_floor, tenants: b.tenant_floors.includes(f) });
  return out;
}

const pick = arr => arr[Math.floor(Math.random() * arr.length)];

// Projector wiring: follows the newest run on the server and feeds the page.
// hooks: reset(runId) when a new run starts, step(d) per trace line, done(facts) at the end (facts may be null).
function projector(hooks) {
  let stop = null;
  followLatest(runId => {
    if (stop) stop();
    hooks.reset(runId);
    stop = streamTrace(runId, hooks.step, hooks.done);
  });
}

// Which data layer a briefing line comes from, for source tags on the projector.
function lineLayer(line) {
  const l = line.toUpperCase();
  if (/^WIND/.test(l)) return "weather";
  if (/^(HYDRANTS|OPEN LOT|ROOF|STAGING|WATER)/.test(l)) return "vexcel";
  if (/^(INSIDE|EXIT SIGNS|WALK|EXITS|FLOOR MAP|3D TOUR)/.test(l) || l.includes("SCAN")) return "matterport";
  if (/^(NOTE|CREW|LAST CREW)/.test(l)) return "notes";
  if (/^(PRIMESIGHT|NOT KNOWN|HTTPS?:)/.test(l)) return "meta";
  return "costar";
}
const LAYER_COLORS = { costar: "#3fa7ff", matterport: "#2ed3a7", vexcel: "#f5c400", weather: "#c084fc", notes: "#ff9f43", meta: "#8b98a5" };
