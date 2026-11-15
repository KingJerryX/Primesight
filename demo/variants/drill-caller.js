// Dispatcher round segment: question the caller. Plugs into drill.html through window.drill (hooks listed there).
// The scripted call is replaced by a live caller (primesight/caller.py): the round opens with a vague, panicked line and
// the player asks for the rest. Each answer can be noted; the grader is told what the player actually heard.
(() => {
  const css = document.createElement("style");
  css.textContent = `
    #caller-box { display: grid; gap: 8px; }
    #caller-form { display: flex; gap: 6px; }
    #caller-q { flex: 1; min-width: 0; padding: 8px 10px; border: 1px solid var(--line); border-radius: 0; background: #05050699; color: var(--text); font: 400 15px var(--grot, inherit); }
    #caller-q:focus { outline: 1px solid var(--accent); }
    #caller-chips { display: flex; flex-wrap: wrap; gap: 6px; }
    #caller-chips .pin { padding: 4px 8px; }
    #call li.caller-q { color: var(--dim); border-left-color: transparent; font-size: 14px; }
    #call li.caller-q::before { content: "You  "; font: 400 11px var(--code); letter-spacing: .07em; text-transform: uppercase; white-space: pre; }
    #call li.caller-wait, #call li.caller-err { color: var(--dim); }
    #call li.caller-err::before { content: none; }
    #call li.caller-wait::after { content: "..."; animation: think 1s infinite; }`;
  document.head.append(css);

  const box = document.createElement("div");
  box.id = "caller-box";
  box.innerHTML = `<form id="caller-form"><input id="caller-q" maxlength="200" placeholder="Ask the caller..." aria-label="Ask the caller" autocomplete="off">
    <button class="pin" id="caller-ask">Ask</button></form><div id="caller-chips"></div>`;
  const q = box.querySelector("#caller-q"), chipsEl = box.querySelector("#caller-chips");
  let round, tools, opening = "", turns = [], busy = false, token = 0;

  const setBusy = b => { busy = b; q.disabled = b; for (const el of box.querySelectorAll("button")) el.disabled = b; };
  const scroll = li => li.scrollIntoView({ block: "nearest" });

  drill.customCall = async (r, t) => {
    const my = ++token;
    round = r; tools = t; opening = ""; turns = []; q.value = ""; chipsEl.innerHTML = "";
    t.callEl.after(box); setBusy(true);
    t.line("911, what's the address of your emergency?", false);
    let o;
    try { o = await api(`/drill/caller/open/${encodeURIComponent(r.case)}/${encodeURIComponent(r.incident.id)}`); }
    catch (e) { o = { opening: r.call[1], chips: [] }; }  // older server: the scripted line, floor and all
    if (my !== token) return;
    opening = o.opening;
    scroll(t.line(opening));
    chipsEl.innerHTML = o.chips.map(c => `<button class="pin" type="button" data-caller-ask="${drill.esc(c)}">${drill.esc(c)}</button>`).join("");
    setBusy(false); q.focus();
  };

  // What the grader is told the caller said: the opening and every question the player asked, with the answer.
  drill.callText = () => [`Caller: ${opening}`, ...turns.map(t => `Q: ${t.q} A: ${t.a}`)].join(" ");

  async function ask(text) {
    text = text.trim().slice(0, 200);
    if (!text || busy || !round) return;
    const my = token;
    setBusy(true); q.value = "";
    tools.line(text, false).classList.add("caller-q");
    const wait = tools.line("", false); wait.classList.add("caller-wait"); scroll(wait);
    let a;
    try { a = (await api("/drill/caller/ask", { case: round.case, incident: round.incident.id, history: turns.slice(-12), question: text })).answer; }
    catch (e) { a = null; }
    if (my !== token) return;
    wait.remove();
    if (a) {
      turns.push({ q: text, a });
      scroll(tools.line(a));
      if (/happen|going on|wrong|emergency/i.test(text) || turns.length >= 2) tools.reveal();
    } else {
      const li = tools.line("The line crackles. Ask again.", false); li.classList.add("caller-q", "caller-err"); scroll(li);
      setTimeout(() => li.remove(), 2500);
    }
    setBusy(false); q.focus();
  }

  box.querySelector("#caller-form").addEventListener("submit", e => { e.preventDefault(); ask(q.value); });
  chipsEl.addEventListener("click", e => { const b = e.target.closest("[data-caller-ask]"); if (b) { b.classList.add("used"); ask(b.dataset.callerAsk); } });
})();
