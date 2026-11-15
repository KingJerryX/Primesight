// Dispatcher round segment: lineup. Plugs into drill.html through window.drill (hooks listed there).
// "Which building": the caller says what the building looks like from the street; the player picks it out of three Vexcel
// aerials (the right one, two decoys). Picking loads it like Locate does; whether it was right shows only in the grade.
// The picked building opens below with the first responder screen's strip: from above and from each side.
// Routes: GET /drill/lineup/<case> (primesight/lineup.py), GET /aerial/<pid>/<view>.jpg (console_api.py, Vexcel on demand).
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
    #lineup-picked { margin: 16px 0 0; max-width: 760px; } #lineup-picked:empty { display: none; }
    #lineup-viewer { margin: 14px 0 0; max-width: 1000px; } #lineup-viewer[hidden] { display: none; }
    #lineup-big { position: relative; height: min(58vh, 560px); background: #0a0d12; }
    #lineup-big img { display: block; width: 100%; height: 100%; object-fit: contain; transition: opacity .2s; } #lineup-big img.loading { opacity: .2; }
    #lineup-big .lineup-miss { position: absolute; inset: 0; display: grid; place-items: center; color: var(--dim); }
    #lineup-strip { display: flex; gap: 6px; margin-top: 8px; overflow-x: auto; }
    #lineup-strip button { position: relative; flex: none; width: 112px; height: 70px; padding: 0; border-radius: 0; overflow: hidden; border: 1px solid var(--line);
                           background: #101820 repeating-linear-gradient(135deg, #ffffff0f 0 1px, #0000 1px 9px); cursor: pointer; }
    #lineup-strip img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }
    #lineup-strip span { position: absolute; inset: auto 0 0; padding: 12px 6px 4px; text-align: left; color: #fff; background: linear-gradient(#0000, #000c); font: 400 11px var(--code); letter-spacing: .06em; text-transform: uppercase; }
    #lineup-strip button:hover { border-color: #fff; } #lineup-strip button[aria-selected="true"] { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent); }`;
  document.head.append(css);

  const view = document.createElement("div");
  view.innerHTML = `<h2>Which building is it?</h2>
    <p class="note">What the caller says it looks like from the street. Each photo is from above, the building at the circle. Click one to see it from every side; the one you leave selected is your pick.</p>
    <blockquote id="lineup-clue">Asking the caller...</blockquote><div id="lineup-grid"></div><p id="lineup-picked" class="dim"></p>
    <div id="lineup-viewer" hidden><div id="lineup-big"><img alt=""></div><div id="lineup-strip" role="tablist" aria-label="Views of the building"></div></div>`;
  drill.addTab("lineup", "Which building", "Vexcel", view, { first: true });
  const $ = id => document.getElementById(id), esc = drill.esc;
  let cands = [], shown = null;
  const VIEWS = [["top", "Top"], ["north", "North"], ["east", "East"], ["south", "South"], ["west", "West"]];
  const big = () => $("lineup-big").querySelector("img");

  // The views of building i, like the first responder screen: a big picture and a strip of thumbnails to switch it.
  function openViews(i) {
    const c = cands[i];
    if (shown === c.pid) return;
    shown = c.pid;
    $("lineup-strip").innerHTML = VIEWS.map(([v, name]) => `<button role="tab" data-view="${v}" aria-selected="${v === "top"}">
      <img src="${esc(v === "top" ? API + c.image : `${API}/aerial/${c.pid}/${v}.jpg`)}" alt="" loading="lazy"><span>${name}</span></button>`).join("");
    $("lineup-strip").querySelectorAll("img").forEach(im => im.onerror = () => im.closest("button").hidden = true);  // no photo from that side
    showView(c, "top", `Building ${"ABC"[i]}`);
    $("lineup-viewer").hidden = false;
    $("lineup-viewer").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
  function showView(c, v, label = big().dataset.label) {
    $("lineup-big").querySelector(".lineup-miss")?.remove();
    const im = big();
    im.classList.add("loading"); im.dataset.label = label;
    im.alt = `${label} ${v === "top" ? "from above" : `from the ${v}`}`;
    im.onload = () => im.classList.remove("loading");
    im.onerror = () => { im.classList.remove("loading"); im.removeAttribute("src"); $("lineup-big").insertAdjacentHTML("beforeend", `<div class="lineup-miss">No photo from this side</div>`); };
    im.src = v === "top" ? API + c.image : `${API}/aerial/${c.pid}/${v}.jpg`;
    $("lineup-strip").querySelectorAll("button").forEach(b => b.setAttribute("aria-selected", b.dataset.view === v));
  }
  $("lineup-strip").addEventListener("click", e => {
    const b = e.target.closest("[data-view]"), c = cands.find(c => c.pid === shown);
    if (b && c) showView(c, b.dataset.view);
  });

  drill.on("start", async round => {
    cands = []; shown = null; $("lineup-viewer").hidden = true; $("lineup-grid").innerHTML = ""; $("lineup-picked").textContent = ""; $("lineup-clue").textContent = "Asking the caller...";
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
    openViews(i);
    await drill.pick(c.pid);
    if (drill.building?.pid !== c.pid) return $("lineup-picked").textContent = "That building's record could not load.";
    drill.addNote(c.address);
    $("lineup-picked").textContent = `Selected ${"ABC"[i]}: ${[c.name, c.address].filter(Boolean).join(", ")}`;
  });

  // Picked here or in Locate: mark the card of whatever building the other screens now show.
  drill.on("picked", b => {
    for (const el of document.querySelectorAll(".lineup-card")) el.classList.toggle("lineup-on", cands[+el.dataset.lineup]?.pid === b.pid);
    if (!cands.some(c => c.pid === b.pid)) { $("lineup-picked").textContent = ""; $("lineup-viewer").hidden = true; shown = null; }
  });
})();
