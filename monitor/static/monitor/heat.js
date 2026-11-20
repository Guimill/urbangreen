/* Heat islands: Landsat surface temperature on 200 m cells, compared with the commune,
   with the same season of other years, or shown as is; plus vegetation vs temperature. */
(function () {
  "use strict";
  const mapHost = document.getElementById("heat-map");
  if (!mapHost) return;
  const config = JSON.parse(document.getElementById("city-config").textContent);
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const nf = (d) => new Intl.NumberFormat("fr-FR", { minimumFractionDigits: d, maximumFractionDigits: d });
  const deg = (v, signed) => (v === null || v === undefined ? "–" : (signed && v > 0 ? "+" : "") + nf(1).format(v) + " °C");
  const pct = (v) => (v === null || v === undefined ? "–" : nf(0).format(v * 100) + " %");

  // Diverging: blue (cooler) – neutral grey – red (hotter). Sequential: one orange hue, light -> dark.
  const DIVERGING = ["#2166ac", "#67a9cf", "#d1e5f0", "#ecebe6", "#fddbc7", "#ef8a62", "#b2182b"];
  const SEQUENTIAL = ["#fff5eb", "#fdd0a2", "#fd8d3c", "#d94801", "#7f2704"];
  const MODES = {
    city: { ramp: DIVERGING, range: [-6, 6], signed: true,
            legend: "Écart à la moyenne de la commune", low: "plus frais que la commune", high: "îlot de chaleur" },
    normal: { ramp: DIVERGING, range: [-8, 8], signed: true,
              legend: "Écart à la même saison des autres années", low: "plus frais que d'habitude", high: "plus chaud que d'habitude" },
    lst: { ramp: SEQUENTIAL, range: null, signed: false, legend: "Température de surface", low: "", high: "" },
  };
  const X_LABELS = { ndvi: "NDVI de la saison", tree_share: "Part d'arbres (OSM)", green_share: "Part d'espaces verts (OSM)" };

  function color(ramp, t) {
    const x = Math.min(Math.max(t, 0), 1) * (ramp.length - 1);
    const i = Math.min(Math.floor(x), ramp.length - 2), f = x - i;
    const a = ramp[i].match(/\w\w/g).map((h) => parseInt(h, 16));
    const b = ramp[i + 1].match(/\w\w/g).map((h) => parseInt(h, 16));
    return `rgb(${a.map((v, k) => Math.round(v + (b[k] - v) * f)).join(",")})`;
  }
  const quantile = (sorted, q) => sorted.length ? sorted[Math.min(sorted.length - 1, Math.floor(q * sorted.length))] : null;

  fetch(config.urls.heat).then((r) => (r.ok ? r.json() : null)).then((ov) => ov && init(ov));

  function init(ov) {
    const mode = document.getElementById("heat-mode");
    const period = document.getElementById("heat-period");
    const dateSelect = document.getElementById("heat-date");
    const dateWrap = document.getElementById("heat-date-wrap");
    const opacity = document.getElementById("heat-opacity");
    const legend = document.getElementById("heat-legend");
    const tiles = document.getElementById("heat-tiles");
    const xSelect = document.getElementById("heat-x");
    const clockHost = document.getElementById("heat-clocks");

    // ---------------------------------------------------------------- season clock
    const seasons = ov.seasons.map((s) => ({ year: +s.key.slice(0, 4), season: s.key.slice(5) }));
    const summers = seasons.filter((s) => s.season === "summer");
    const clocks = SeasonClocks(ov.seasons, ov.dates.map((d) => d.date),
                                { single: summers.length ? summers[summers.length - 1] : seasons[seasons.length - 1] });
    clocks.clock(clockHost, "single");

    dateSelect.innerHTML = ov.dates.map((d) => `<option value="${d.date}">${new Date(d.date + "T12:00:00").toLocaleDateString("fr-FR")} · ${deg(d.air_tmax)}</option>`).join("");
    dateSelect.value = ov.dates[ov.dates.length - 1].date;

    // ---------------------------------------------------------------- map and cells
    const map = L.map(mapHost, { scrollWheelZoom: false, zoomSnap: 0.25 });
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> · Landsat : USGS/NASA',
    }).addTo(map);
    const renderer = L.canvas({ padding: 0.2 });
    const g = ov.grid;
    const unproject = (x, y) => L.Projection.SphericalMercator.unproject(L.point(x, y));
    const cells = [];   // {i, j, layer}
    const group = L.featureGroup().addTo(map);
    for (let j = 0; j < g.ny; j++) {
      for (let i = 0; i < g.nx; i++) {
        if (!ov.cells[j][i]) continue;
        const x = g.x0 + i * g.res, y = g.y1 - j * g.res;
        const rect = L.rectangle([unproject(x, y - g.res), unproject(x + g.res, y)],
                                 { renderer, weight: 0, fillOpacity: 0, interactive: true });
        rect.bindTooltip(() => tooltip(i, j), { sticky: true });
        rect.on("mouseover", () => rect.setStyle({ weight: 2, color: "#1a1a19" }));
        rect.on("mouseout", () => rect.setStyle({ weight: 0 }));
        cells.push({ i, j, rect });
        group.addLayer(rect);
      }
    }
    map.fitBounds(group.getBounds(), { padding: [10, 10] });

    let layer = null;     // last layer JSON
    let scale = [0, 1];
    let request = 0;

    function key() {
      return period.value === "date" ? dateSelect.value : clocks.keyOf(clocks.state.single);
    }

    function layout() {
      // the "other years" comparison only exists for a whole season
      period.querySelector('option[value="date"]').disabled = mode.value === "normal";
      if (mode.value === "normal") period.value = "season";
      dateWrap.hidden = period.value !== "date";
      clockHost.hidden = period.value !== "season";
      map.invalidateSize();
    }

    async function show() {
      const id = ++request;
      const url = config.urls.heat_layer.replace("{mode}", mode.value).replace("{key}", key());
      let data = null, error = "";
      try {
        const response = await fetch(url);
        if (response.ok) data = await response.json();
        else error = response.status === 429 ? "Trop de demandes : réessayez plus tard." : "Pas de données pour cette période.";
      } catch (e) {
        error = "Données non disponibles.";
      }
      if (id !== request) return;   // overtaken by a newer choice
      layer = data;
      if (!data || !data.values) {
        cells.forEach((c) => c.rect.setStyle({ fillOpacity: 0 }));
        legend.innerHTML = `<span class="legend__warn" role="status">${error || data.message}</span>`;
        tiles.innerHTML = "";
        renderScatter();
        return;
      }
      paint();
      renderTiles();
      renderScatter();
    }

    function finiteValues() {
      const out = [];
      cells.forEach((c) => { const v = layer.values[c.j][c.i]; if (v !== null) out.push(v); });
      return out.sort((a, b) => a - b);
    }

    function paint() {
      const m = MODES[mode.value];
      const sorted = finiteValues();
      scale = m.range || [Math.floor(quantile(sorted, 0.02) / 5) * 5, Math.ceil(quantile(sorted, 0.98) / 5) * 5];
      const [lo, hi] = scale;
      cells.forEach((c) => {
        const v = layer.values[c.j][c.i];
        c.rect.setStyle(v === null ? { fillOpacity: 0 }
          : { fillColor: color(m.ramp, (v - lo) / (hi - lo)), fillOpacity: +opacity.value });
      });
      const name = period.value === "date"
        ? new Date(dateSelect.value + "T12:00:00").toLocaleDateString("fr-FR", { dateStyle: "long" })
        : `${clocks.nameOf(clocks.state.single)} (${layer.dates.length} date${layer.dates.length > 1 ? "s" : ""})`;
      const others = mode.value === "normal" ? ` · normale : ${layer.other_years.length} autre${layer.other_years.length > 1 ? "s" : ""} année${layer.other_years.length > 1 ? "s" : ""} (${layer.other_years.join(", ")})` : "";
      legend.innerHTML = `
        <span>${m.legend} · ${name}${others}</span>
        <span><span class="legend__bar" style="display:block;background:linear-gradient(to right, ${m.ramp.join(",")})"></span>
        <span class="legend__scale"><span>${deg(lo, m.signed)}</span><span>${m.signed ? "0" : deg((lo + hi) / 2)}</span><span>${deg(hi, m.signed)}</span></span></span>
        ${m.low ? `<span class="muted">bleu : ${m.low} · rouge : ${m.high}</span>` : ""}
        <span class="muted">carrés de ${g.size_m} m · transparent : nuages ou eau</span>`;
    }

    function tooltip(i, j) {
      const parts = [];
      if (layer && layer.values) {
        const v = layer.values[j][i];
        const m = MODES[mode.value];
        parts.push(v === null ? "pas de mesure (nuages, eau)" : `<strong>${deg(v, m.signed)}</strong> ${m.signed ? (mode.value === "city" ? "par rapport à la commune" : "par rapport aux autres années") : ""}`);
        if (layer.ndvi && layer.ndvi[j][i] !== null) parts.push(`NDVI ${nf(2).format(layer.ndvi[j][i])}`);
      }
      parts.push(`arbres OSM ${pct(ov.tree_share[j][i])} · espaces verts OSM ${pct(ov.green_share[j][i])}`);
      return parts.join("<br>");
    }

    // ---------------------------------------------------------------- key figures
    function tile(label, value, hint) {
      return `<div class="tile"><span class="tile__label">${label}</span><span class="tile__value">${value}</span><span class="tile__hint">${hint}</span></div>`;
    }

    function renderTiles() {
      const sorted = finiteValues();
      const out = [];
      if (mode.value === "city") {
        const p10 = quantile(sorted, 0.1), p90 = quantile(sorted, 0.9);
        out.push(tile("Contraste chaud / frais", deg(p90 - p10), "écart entre les 10 % de carrés les plus chauds et les 10 % les plus frais"));
        out.push(tile("Carré le plus chaud", deg(sorted[sorted.length - 1], true), "au-dessus de la moyenne de la commune (survolez la carte)"));
      } else if (mode.value === "normal") {
        out.push(tile("Écart médian des carrés", deg(quantile(sorted, 0.5), true), "par rapport à la même saison des autres années"));
      } else {
        out.push(tile("Température de surface médiane", deg(quantile(sorted, 0.5)), `de ${deg(quantile(sorted, 0.1))} à ${deg(quantile(sorted, 0.9))} (10 % – 90 %)`));
      }
      if (layer.air_tmax !== null && layer.normal_tmax !== null) {
        out.push(tile("Air, max. des jours d'image", deg(layer.air_tmax),
                      `normale 1991-2020 : ${deg(layer.normal_tmax)} (${deg(layer.air_tmax - layer.normal_tmax, true)}). Landsat ne voit que les jours dégagés, plus chauds que la moyenne.`));
      }
      const fit = regression();
      if (fit) {
        // NDVI and shares both go from 0 to 1: the slope is given for a step of 0.1 (10 points)
        const unit = xSelect.value === "ndvi" ? "+0,1 de NDVI" : "+10 points de " + (xSelect.value === "tree_share" ? "couvert arboré" : "espaces verts");
        out.push(tile("Effet de la végétation", deg(fit.slope * 0.1, true), `par ${unit} (r² = ${nf(2).format(fit.r2)}, ${fit.n} carrés)`));
      }
      tiles.innerHTML = out.join("");
    }

    // ---------------------------------------------------------------- vegetation vs temperature
    function points() {
      if (!layer || !layer.values) return [];
      const xs = xSelect.value === "ndvi" ? layer.ndvi : ov[xSelect.value];
      if (!xs) return [];
      const out = [];
      cells.forEach((c) => {
        const x = xs[c.j][c.i], y = layer.values[c.j][c.i];
        if (x !== null && y !== null) out.push({ x, y });
      });
      return out;
    }

    function regression() {
      const pts = points();
      if (pts.length < 10) return null;
      const n = pts.length;
      const mx = pts.reduce((s, p) => s + p.x, 0) / n, my = pts.reduce((s, p) => s + p.y, 0) / n;
      let sxy = 0, sxx = 0, syy = 0;
      pts.forEach((p) => { sxy += (p.x - mx) * (p.y - my); sxx += (p.x - mx) ** 2; syy += (p.y - my) ** 2; });
      if (!sxx || !syy) return null;
      const slope = sxy / sxx;
      return { slope, intercept: my - slope * mx, r2: (sxy * sxy) / (sxx * syy), n };
    }

    let chart = null;
    function renderScatter() {
      const canvas = document.getElementById("heat-scatter");
      const pts = points();
      document.getElementById("heat-scatter-empty").hidden = !(xSelect.value === "ndvi" && layer && layer.values && !layer.ndvi);
      const fit = regression();
      const xs = pts.map((p) => p.x);
      const xmin = Math.min(...xs), xmax = Math.max(...xs);
      const m = MODES[mode.value];
      const percentX = xSelect.value !== "ndvi";
      const datasets = [{
        type: "scatter", label: "Carrés de 200 m", data: pts,
        backgroundColor: pts.map((p) => color(m.ramp, (p.y - scale[0]) / (scale[1] - scale[0]))),
        borderColor: css("--surface"), borderWidth: 1, pointRadius: 3.5, pointHoverRadius: 6,
      }];
      if (fit) datasets.push({
        type: "line", label: "Tendance", data: [{ x: xmin, y: fit.intercept + fit.slope * xmin }, { x: xmax, y: fit.intercept + fit.slope * xmax }],
        borderColor: css("--ink-2"), borderWidth: 2, pointRadius: 0, borderDash: [6, 4],
      });
      const cfg = {
        data: { datasets },
        options: {
          maintainAspectRatio: false, animation: false,
          plugins: {
            legend: { display: false },
            tooltip: { filter: (item) => item.datasetIndex === 0, callbacks: {
              label: (ctx) => ` ${X_LABELS[xSelect.value]} ${percentX ? pct(ctx.parsed.x) : nf(2).format(ctx.parsed.x)} · ${deg(ctx.parsed.y, m.signed)}`,
            } },
          },
          scales: {
            x: { type: "linear", grid: { color: css("--grid") }, border: { color: css("--axis") },
                 title: { display: true, text: X_LABELS[xSelect.value], color: css("--muted") },
                 ticks: { color: css("--muted"), callback: (v) => (percentX ? nf(0).format(v * 100) + " %" : nf(1).format(v)) } },
            y: { grid: { color: css("--grid") }, border: { display: false },
                 title: { display: true, text: m.legend + " (°C)", color: css("--muted") },
                 ticks: { color: css("--muted"), callback: (v) => (m.signed && v > 0 ? "+" : "") + nf(0).format(v) } },
          },
        },
      };
      if (chart) chart.destroy();
      chart = new Chart(canvas, cfg);
    }

    // ---------------------------------------------------------------- wiring
    mode.addEventListener("change", () => { layout(); show(); });
    period.addEventListener("change", () => { layout(); show(); });
    dateSelect.addEventListener("change", show);
    clocks.onChange(() => period.value === "season" && show());
    opacity.addEventListener("input", () => layer && layer.values && paint());
    xSelect.addEventListener("change", () => { if (layer && layer.values) { renderTiles(); renderScatter(); } });
    document.querySelectorAll(".show-heat-date").forEach((link) => link.addEventListener("click", () => {
      if (mode.value === "normal") mode.value = "city";
      period.value = "date"; dateSelect.value = link.dataset.heatDate; layout(); show();
    }));
    layout();
    show();
  }
})();
