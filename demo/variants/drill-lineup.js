// Dispatcher round segment: lineup. Plugs into drill.html through window.drill (hooks listed there).
// "Which building": the caller says what the building looks like from the street; the player picks it out of three Vexcel
// aerials (the right one, two decoys). Picking loads it like Locate does; whether it was right shows only in the grade.
// Route: GET /drill/lineup/<case> (primesight/lineup.py).
(() => {
  const css = document.createElement("style");
  css.textContent = `
    #lineup-clue { margin: 0 0 18px; padding: 10px 14px; border-left: 2px solid var(--accent); max-width: 760px; font-size: 18px; }
    #lineup-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 14px; max-width: 1000px; }
    .lineup-card { display: grid; gap: 8px; padding: 0; border: 1px solid var(--line); border-radius: 0; background: none; color: var(--text); text-align: left; cursor: pointer; }
    .lineup-card:hover { border-color: var(--text); } .lineup-card.lineup-on { border-color: var(--ok); }
    .lineup-img { position: relative; aspect-ratio: 1; overflow: hidden; background: #0a0d12; }
    .lineup-img img { display: block; width: 100%; height: 100%; object-fit: cover; transform: scale(1.35); }
    .lineup-img::after { content: ""; position: absolute; left: 50%; top: 50%; width: 28px; height: 28px; margin: -14px 0 0 -14px; border: 1px solid #fff9; border-radius: 50%; }
    .lineup-card .caps { padding: 0 10px 10px; } .lineup-card.lineup-on .caps { color: var(--ok); }
    #lineup-picked { margin: 16px 0 0; max-width: 760px; } #lineup-picked:empty { display: none; }`;
  document.head.append(css);

  const view = document.createElement("div");
  view.innerHTML = `<h2>Which building is it?</h2>
    <p class="note">What the caller says it looks like from the street. Each photo is from above, the building at the circle. Pick the one they mean.</p>
    <blockquote id="lineup-clue">Asking the caller...</blockquote><div id="lineup-grid"></div><p id="lineup-picked" class="dim"></p>`;
  drill.addTab("lineup", "Which building", "Vexcel", view, { first: true });
  const $ = id => document.getElementById(id), esc = drill.esc;
  let cands = [];

  drill.on("start", async round => {
    cands = []; $("lineup-grid").innerHTML = ""; $("lineup-picked").textContent = ""; $("lineup-clue").textContent = "Asking the caller...";
    let r;
    try { r = await api(`/drill/lineup/${round.case}`); } catch (e) { $("lineup-clue").textContent = "No description from the caller. Use Locate."; return; }
    if (drill.round !== round) return;  // a newer round started while this loaded
    cands = r.candidates;
    $("lineup-clue").innerHTML = `&ldquo;${esc(r.clue)}&rdquo;`;
    $("lineup-grid").innerHTML = cands.map((c, i) => `<button class="lineup-card" data-lineup="${i}" aria-label="Building ${"ABC"[i]}">
      <div class="lineup-img"><img src="${esc(API + c.image)}" alt="Building ${"ABC"[i]} from above" loading="lazy"></div>
      <span class="caps">Building ${"ABC"[i]}</span></button>`).join("");
  });

  $("lineup-grid").addEventListener("click", async e => {
    const b = e.target.closest("[data-lineup]"); if (!b) return;
    const i = +b.dataset.lineup, c = cands[i];
    await drill.pick(c.pid);
    if (drill.building?.pid !== c.pid) return $("lineup-picked").textContent = "That building's record could not load.";
    drill.addNote(c.address);
    $("lineup-picked").textContent = `Selected ${"ABC"[i]}: ${[c.name, c.address].filter(Boolean).join(", ")}`;
  });

  // Picked here or in Locate: mark the card of whatever building the other screens now show.
  drill.on("picked", b => {
    for (const el of document.querySelectorAll(".lineup-card")) el.classList.toggle("lineup-on", cands[+el.dataset.lineup]?.pid === b.pid);
    if (!cands.some(c => c.pid === b.pid)) $("lineup-picked").textContent = "";
  });
})();
