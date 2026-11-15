// Dispatcher round segment: beat Primesight. Plugs into drill.html through window.drill (hooks listed there).
// On the result screen: the player's grade next to Primesight's own crew text for the same building, floor and emergency,
// graded by the same grader on the same call. Primesight's run is precomputed (POST /drill/rival reads cache/rival/), so
// nothing here waits on a model; with no cached run the block just doesn't appear.
(() => {
  const css = document.createElement("style");
  css.textContent = `
    #rival { max-width: 1100px; margin: 4px 0 30px; padding-top: 22px; border-top: 1px solid var(--line); }
    #rival .rival-verdict { font: 400 clamp(26px, 2.8vw, 38px)/1.15 var(--grot, inherit); letter-spacing: -.02em; margin: 6px 0 4px; }
    #rival .rival-verdict.rival-win { color: var(--ok); } #rival .rival-verdict.rival-lose { color: var(--accent); }
    #rival .rival-note { margin: 0 0 16px; color: var(--dim); font-size: 14px; max-width: 60em; }
    #rival table { width: 100%; max-width: 620px; border-collapse: collapse; margin: 0 0 22px; font-variant-numeric: tabular-nums; }
    #rival th, #rival td { padding: 8px 10px; border-bottom: 1px solid var(--line); text-align: right; font-weight: 400; }
    #rival th:first-child, #rival td:first-child { text-align: left; }
    #rival thead th { color: var(--dim); font: 400 11px var(--code); letter-spacing: .07em; text-transform: uppercase; }
    #rival td { font: 400 17px var(--code); } #rival td:first-child { font: 400 15px var(--grot, inherit); }
    #rival td.rival-best { color: var(--ok); }
    #rival tfoot td { font-size: 22px; border-bottom: 0; }
    #rival .rival-texts { display: grid; grid-template-columns: 1fr 1fr; gap: 28px; }
    #rival .rival-texts h3 { margin: 0 0 8px; font: 400 15px var(--grot, inherit); color: var(--dim); }
    #rival .rival-texts pre { margin: 0; white-space: pre-wrap; padding: 12px 14px; border: 1px solid var(--line); font: 400 14px/1.5 var(--code); }
    #rival .rival-sent { margin: 8px 0 0; color: var(--dim); font-size: 13px; }
    @media (max-width: 860px) { #rival .rival-texts { grid-template-columns: 1fr; } }`;
  document.head.append(css);

  const esc = s => drill.esc(s);
  const NAMES = { location: ["Location", 28], emergency: ["Emergency", 8], needs: ["Needed facts", 20], accuracy: ["Accuracy", 12], delivery: ["Delivery", 12], speed: ["Speed", 20] };
  let token = 0;

  drill.on("result", async ({ g, message, el, round }) => {
    const my = ++token;
    let r;
    try { r = await api("/drill/rival", { case: round.case, incident: round.incident.id }); }
    catch (e) { return; }  // no cached Primesight run for this call (404) or an older server: show nothing
    if (my !== token || !el.isConnected || !r || typeof r.text !== "string") return;
    // Only the parts both grades have: with the grading team down, a grade is the 70 code points alone.
    const keys = Object.keys(NAMES).filter(k => k in (g.parts || {}) && k in (r.parts || {}));
    const sum = parts => keys.reduce((t, k) => t + (+parts[k] || 0), 0), out = keys.reduce((t, k) => t + NAMES[k][1], 0);
    const you = sum(g.parts), them = sum(r.parts), d = you - them;
    const verdict = d > 0 ? `You beat Primesight by ${d}` : d < 0 ? `Primesight wins by ${-d}` : "A tie with Primesight";
    const cell = (a, b) => `<td class="${a > b ? "rival-best" : ""}">${a}</td>`;
    const rows = keys.map(k => `<tr><td>${esc(NAMES[k][0])} <span class="dim">/ ${NAMES[k][1]}</span></td>${cell(+g.parts[k] || 0, +r.parts[k] || 0)}${cell(+r.parts[k] || 0, +g.parts[k] || 0)}</tr>`).join("");
    const took = typeof r.secs === "number" ? ` Primesight took ${Math.round(r.secs)} s to write it; its Speed part counts that time the same way yours does.` : "";
    const sec = document.createElement("section");
    sec.id = "rival";
    sec.innerHTML = `<div class="caps dim">Beat Primesight</div>
      <div class="rival-verdict ${d > 0 ? "rival-win" : d < 0 ? "rival-lose" : ""}" aria-live="polite">${esc(verdict)}</div>
      <p class="rival-note">Primesight is this project's AI that texts crews a building briefing. It wrote its text for the same building, floor and emergency,
        and the same graders scored it against the same call.${r.source === "draft" ? " This is its no-AI draft, written from the building data alone." : ""}
        It heard the same caller you did.${esc(took)}</p>
      <table><thead><tr><th>Part</th><th>You</th><th>Primesight</th></tr></thead><tbody>${rows}</tbody>
        <tfoot><tr><td>Total <span class="dim">/ ${out}</span></td>${cell(you, them)}${cell(them, you)}</tr></tfoot></table>
      <div class="rival-texts">
        <div><h3 class="caps">Your message</h3><pre>${esc(message)}</pre></div>
        <div><h3 class="caps">Primesight's text${r.source === "draft" ? " (draft)" : ""}</h3><pre>${esc(r.text)}</pre>
          <p class="rival-sent">Primesight was sent: ${esc(r.sent)}</p></div>
      </div>`;
    const again = el.querySelector("#again");
    again ? again.before(sec) : el.append(sec);
  });
})();
