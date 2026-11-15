// Dispatcher round segment: the crew texts back. Plugs into drill.html through window.drill (hooks listed there).
// While the graders run, Engine 1 texts one follow-up (GET /drill/crew/<case>/<incident>); the player has 30 s to answer
// from their notes; POST /drill/crew/check says if it matches the record. The result screen gets a "Crew follow-up" box.
(() => {
  const ANSWER_S = 30, esc = drill.esc;
  const css = document.createElement("style");
  css.textContent = `
    .crew-reply { display: flex; gap: 8px; align-items: center; padding: 10px 12px 18px; border-top: 1px solid #ffffff1c; animation: pop .35s var(--ease) both; }
    .crew-reply input { flex: 1; min-width: 0; padding: 8px 12px; border: 1px solid #ffffff38; border-radius: 18px; background: #111214; color: var(--text); font: 400 14px var(--grot, inherit); }
    .crew-reply input:focus { outline: 1px solid var(--accent); }
    .crew-clock { font: 400 13px var(--code); color: var(--dim); min-width: 3ch; text-align: right; font-variant-numeric: tabular-nums; }
    .crew-clock.crew-late { color: var(--accent); }
    .crew-box { max-width: 900px; margin: 0 0 26px; padding: 12px 14px; border: 1px solid var(--line); }
    .crew-box h2 { font-size: 15px; margin: 0 0 8px; }
    .crew-box p { margin: 4px 0; } .crew-box .crew-ok { color: var(--ok); } .crew-box .crew-bad { color: var(--accent); }`;
  document.head.append(css);

  let fu = null, run = 0;  // this round's follow-up; run drops a stale one if the player sends again
  const clear = () => { document.querySelector(".crew-reply")?.remove(); };
  drill.on("start", () => { fu = null; run++; clear(); });

  drill.on("sent", ({ thread, round }) => {
    const me = ++run; fu = null; clear();
    const ask = api(`/drill/crew/${round.case}/${round.incident.id}`).catch(() => null);
    const bubble = (cls, html) => { const d = document.createElement("div"); d.className = "msg " + cls; d.innerHTML = html; thread.append(d); return d; };
    return new Promise(done => setTimeout(async () => {
      const q = await ask;
      if (!q || me !== run) return done();
      thread.querySelector(".dots")?.remove();
      bubble("it", esc(q.question));
      const form = document.createElement("form");
      form.className = "crew-reply";
      form.innerHTML = `<input maxlength="300" placeholder="Reply to Engine 1" aria-label="Reply to Engine 1" autocomplete="off"><button class="pin">Send</button><output class="crew-clock">${ANSWER_S}</output>`;
      thread.after(form);
      const input = form.querySelector("input"), out = form.querySelector("output"), t0 = performance.now();
      input.focus({ preventScroll: true });
      const tick = setInterval(() => {
        const left = ANSWER_S - (performance.now() - t0) / 1000;
        out.textContent = Math.max(0, Math.ceil(left)); out.classList.toggle("crew-late", left <= 10);
        if (left <= 0) finish("");
      }, 250);
      let sent = false;
      async function finish(answer) {
        if (sent) return; sent = true; clearInterval(tick);
        const secs = (performance.now() - t0) / 1000;
        form.remove();
        if (answer) bubble("me", esc(answer));
        const dots = bubble("it dots", "<i></i><i></i><i></i>");
        let r;
        try { r = await api("/drill/crew/check", { case: round.case, incident: round.incident.id, qid: q.qid, answer }); }
        catch (e) { r = { ok: false, expected: "", why: "the check could not reach the server", reply: "Copy, we'll check on scene." }; }
        dots.remove();
        if (me !== run) return done();
        bubble("it", esc(r.reply));
        fu = { question: q.question, answer, secs: answer ? secs : null, ...r };
        setTimeout(done, 1200);  // let them read the crew's reply before the result covers it
      }
      form.addEventListener("submit", e => { e.preventDefault(); const a = input.value.trim(); if (a) finish(a); });
    }, 6000 + Math.random() * 2000));
  });

  drill.on("result", ({ el }) => {
    if (!fu) return;
    const box = document.createElement("div");
    box.className = "crew-box";
    box.innerHTML = `<h2 class="caps dim">Crew follow-up</h2>
      <p><span class="dim">Engine 1:</span> ${esc(fu.question)}</p>
      <p><span class="dim">You:</span> ${fu.answer ? esc(fu.answer) : `<span class="dim">no reply in ${ANSWER_S} s</span>`}</p>
      <p class="${fu.ok ? "crew-ok" : "crew-bad"}">${fu.ok ? "Right" : "Wrong"}${fu.expected ? `: records show ${esc(fu.expected)}` : ""}${fu.why && fu.by === "model" || !fu.ok && fu.answer ? ` <span class="dim">(${esc(fu.why)})</span>` : ""}</p>
      <p class="dim">${fu.secs != null ? `Answered in ${Math.round(fu.secs)} s of ${ANSWER_S}. ` : ""}Not part of the score.</p>`;
    const parts = el.querySelector(".parts");  // under the score, where it's seen; else at the end
    parts ? parts.after(box) : el.append(box);
  });
})();
