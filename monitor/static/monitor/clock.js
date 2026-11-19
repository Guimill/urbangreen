/* Season clock: picks a (season, year) on a clock face instead of a dropdown.

   The face is a year: December sits at 12 o'clock and every month is one "hour", so
   the four meteorological seasons are the four quarters (winter = Dec–Feb around
   1 o'clock, spring around 4, summer around 7, autumn around 10). Clicking a quarter
   moves the hand there; the arrows below change the year; the dots on the rim are the
   cloud-free dates available in that year. Arrow keys walk through the available
   seasons in time order, across years.

   SeasonClocks(seasons, dates, initial) -> { state, set, clock(host, slot, tag), onChange, swap }
     seasons: [{key: "2026-summer", count: 7}, …] in time order
     dates:   ["2026-07-03", …]
     initial: {slot: {year, season}, …}
*/
(function () {
  "use strict";
  const SEASON_KEYS = ["winter", "spring", "summer", "autumn"];
  const SEASON_NAMES = { winter: "Hiver", spring: "Printemps", summer: "Été", autumn: "Automne" };
  const SEASON_SHORT = { winter: "Hiver", spring: "Print.", summer: "Été", autumn: "Aut." };
  const keyOf = (v) => `${v.year}-${v.season}`;
  const nameOf = (v) => `${SEASON_NAMES[v.season]} ${v.year}`;

  const xy = (r, deg) => {
    const a = (deg * Math.PI) / 180;
    return [60 + r * Math.sin(a), 60 - r * Math.cos(a)].map((x) => +x.toFixed(2));
  };
  const wedge = (r0, r1, a0, a1) => {
    const [p, q, s, t] = [xy(r1, a0), xy(r1, a1), xy(r0, a1), xy(r0, a0)];
    return `M${p}A${r1} ${r1} 0 0 1 ${q}L${s}A${r0} ${r0} 0 0 0 ${t}Z`;
  };
  // angle of a date on the face: December 1st at -15°, each month 30°
  const dateAngle = (iso) => {
    const d = new Date(iso + "T12:00:00");
    const days = new Date(d.getFullYear(), d.getMonth() + 1, 0).getDate();
    return ((d.getMonth() + 1) % 12) * 30 - 15 + ((d.getDate() - 0.5) / days) * 30;
  };
  // the season year runs from December to November (December belongs to next year's winter)
  const seasonYear = (iso) => +iso.slice(0, 4) + (iso.slice(5, 7) === "12" ? 1 : 0);

  window.SeasonClocks = function (seasons, dates, initial) {
    const counts = new Map(seasons.map((s) => [s.key, s.count]));
    const ordered = seasons.map((s) => ({ year: +s.key.slice(0, 4), season: s.key.slice(5) }));
    const years = [...new Set(ordered.map((o) => o.year))].sort((a, b) => a - b);
    const has = (v) => counts.has(keyOf(v));
    const state = Object.assign({}, initial);
    const listeners = [];
    const clocks = [];

    function set(slot, value) {
      state[slot] = value;
      clocks.forEach((c) => c.render());
      listeners.forEach((f) => f(slot));
    }

    // when the year changes keep the season if it has images, otherwise take the nearest one
    function inYear(year, season) {
      const i = SEASON_KEYS.indexOf(season);
      for (const d of [0, 1, -1, 2, -2, 3, -3]) {
        const s = SEASON_KEYS[i + d];
        if (s && has({ year, season: s })) return { year, season: s };
      }
      return null;
    }

    function clock(host, slot, tag) {
      const box = document.createElement("div");
      box.className = "clock";
      box.innerHTML = `
        <div class="clock__head">${tag ? `<span class="clock__tag" aria-hidden="true">${tag}</span>` : ""}<span class="clock__name"></span></div>
        <svg class="clock__face" viewBox="0 0 120 120" role="radiogroup" aria-label="${tag ? "Saison " + tag : "Saison"}"></svg>
        <div class="clock__years">
          <button type="button" class="clock__step" data-step="-1" aria-label="Année précédente${tag ? " (" + tag + ")" : ""}">‹</button>
          <span class="clock__year" aria-live="polite"></span>
          <button type="button" class="clock__step" data-step="1" aria-label="Année suivante${tag ? " (" + tag + ")" : ""}">›</button>
        </div>
        <span class="clock__count"></span>`;
      host.appendChild(box);
      const svg = box.querySelector("svg");
      const steps = box.querySelectorAll(".clock__step");

      steps.forEach((b) => b.addEventListener("click", () => {
        const step = +b.dataset.step, v = state[slot];
        for (let y = v.year + step; y >= years[0] && y <= years[years.length - 1]; y += step) {
          const next = inYear(y, v.season);
          if (next) return set(slot, next);
        }
      }));

      svg.addEventListener("click", (e) => {
        const g = e.target.closest(".clock__season");
        if (g && !g.classList.contains("is-off")) {
          set(slot, { year: state[slot].year, season: g.dataset.season });
          focusOn();
        }
      });
      svg.addEventListener("keydown", (e) => {
        const v = state[slot];
        const i = ordered.findIndex((o) => o.year === v.year && o.season === v.season);
        const move = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
        if (move && ordered[i + move]) {
          e.preventDefault();
          set(slot, ordered[i + move]);
          focusOn();
        }
      });
      const focusOn = () => { const on = svg.querySelector(".is-on"); if (on) on.focus(); };

      function render() {
        const v = state[slot];
        const parts = ['<circle class="clock__rim" cx="60" cy="60" r="58"/>'];
        for (let m = 0; m < 12; m++) {
          const [a, b] = [xy(51, m * 30 - 15), xy(56, m * 30 - 15)];
          parts.push(`<line class="clock__tick${m % 3 ? "" : " clock__tick--season"}" x1="${a[0]}" y1="${a[1]}" x2="${b[0]}" y2="${b[1]}"/>`);
        }
        SEASON_KEYS.forEach((s, i) => {
          const centre = 30 + 90 * i, item = { year: v.year, season: s };
          const ok = has(item), on = s === v.season;
          const [lx, ly] = xy(37.5, centre);
          const n = counts.get(keyOf(item));
          parts.push(`<g class="clock__season${on ? " is-on" : ""}${ok ? "" : " is-off"}" data-season="${s}"
              role="radio" aria-checked="${on}"${ok ? "" : ' aria-disabled="true"'} tabindex="${on ? 0 : -1}"
              aria-label="${nameOf(item)}${ok ? ` (${n} date${n > 1 ? "s" : ""})` : " : pas d'image"}">
            <path d="${wedge(21, 49, centre - 44, centre + 44)}"/>
            <text x="${lx}" y="${ly}">${SEASON_SHORT[s]}</text></g>`);
        });
        dates.filter((d) => seasonYear(d) === v.year).forEach((d) => {
          const [x, y] = xy(53.5, dateAngle(d));
          parts.push(`<circle class="clock__date" cx="${x}" cy="${y}" r="2"><title>${new Date(d + "T12:00:00").toLocaleDateString("fr-FR")}</title></circle>`);
        });
        const angle = 30 + 90 * SEASON_KEYS.indexOf(v.season);
        parts.push(`<g class="clock__hand" style="transform: rotate(${angle}deg)" aria-hidden="true"><path d="M57.4 60 L60 30 L62.6 60 Z"/></g>`,
                   '<circle class="clock__hub" cx="60" cy="60" r="14" aria-hidden="true"/>',
                   `<text class="clock__hub-text" x="60" y="60" aria-hidden="true">${v.year}</text>`);
        // keep the hand element between renders so its rotation animates
        const old = svg.querySelector(".clock__hand");
        svg.innerHTML = parts.join("");
        if (old) {
          const hand = svg.querySelector(".clock__hand");
          old.style.transform = hand.style.transform;
          hand.replaceWith(old);
        }
        box.querySelector(".clock__name").textContent = nameOf(v);
        box.querySelector(".clock__year").textContent = v.year;
        const n = counts.get(keyOf(v));
        box.querySelector(".clock__count").textContent = n ? `${n} date${n > 1 ? "s" : ""} sans nuage` : "pas d'image";
        steps[0].disabled = !years.some((y) => y < v.year);
        steps[1].disabled = !years.some((y) => y > v.year);
      }

      const c = { render, box };
      clocks.push(c);
      render();
      return c;
    }

    return {
      state, set, clock, has,
      keyOf, nameOf,
      onChange: (f) => listeners.push(f),
      swap(a, b) { const t = state[a]; state[a] = state[b]; set(b, t); },
    };
  };
})();
