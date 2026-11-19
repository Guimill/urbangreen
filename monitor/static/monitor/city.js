/* City page: live status, map overlays, seasonal table and charts. */
(function () {
  "use strict";
  const config = JSON.parse(document.getElementById("city-config").textContent);
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  // ------------------------------------------------------------------ status
  const statusBox = document.getElementById("status");
  const statusText = document.getElementById("status-text");
  if (config.running) pollStatus();

  function pollStatus() {
    setTimeout(async () => {
      try {
        const s = await (await fetch(config.urls.status, { cache: "no-store" })).json();
        statusText.textContent = s.status === "error" ? "Erreur : " + s.message : s.message;
        if (s.running) return pollStatus();
        if (s.status === "interrupted") {
          statusBox.className = "status status--error";
          statusText.textContent = "L'analyse a été interrompue (serveur redémarré ?). Relancez-la.";
          return;
        }
        window.location.reload();
      } catch (e) {
        pollStatus();
      }
    }, 4000);
  }

  if (!document.getElementById("map")) return; // no data yet

  // ------------------------------------------------------------------ helpers
  const METRICS = {
    green_share: { label: "Part végétalisée", percent: true, range: [0, 1] },
    dense_share: { label: "Végétation dense", percent: true, range: [0, 1] },
    mean_ndvi: { label: "NDVI moyen", percent: false, range: [0, 0.9] },
  };
  const nf = (digits) => new Intl.NumberFormat("fr-FR", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  const fmt = (value, metric, signed = false) => {
    if (value === null || value === undefined) return "–";
    const m = METRICS[metric];
    const sign = signed && value > 0 ? "+" : "";
    return m.percent ? sign + nf(0).format(value * 100) + " %" : sign + nf(2).format(value);
  };
  const fmtDiff = (value, metric) => {
    if (value === null || value === undefined) return "–";
    const sign = value > 0 ? "+" : "";
    return METRICS[metric].percent ? sign + nf(0).format(value * 100) + " pts" : sign + nf(2).format(value);
  };
  const catColor = (key) => css("--cat-" + key);
  const metricSelect = document.getElementById("metric");

  // ramp "#hex, #hex, ..." -> colour at t in [0, 1]
  function rampColor(stops, t) {
    const hex = stops.split(",").map((s) => s.trim());
    const x = Math.min(Math.max(t, 0), 1) * (hex.length - 1);
    const i = Math.min(Math.floor(x), hex.length - 2), f = x - i;
    const a = hex[i].match(/\w\w/g).map((h) => parseInt(h, 16));
    const b = hex[i + 1].match(/\w\w/g).map((h) => parseInt(h, 16));
    const c = a.map((v, k) => Math.round(v + (b[k] - v) * f));
    return { css: `rgb(${c.join(",")})`, dark: 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2] < 140 };
  }

  fetch(config.urls.data).then((r) => r.json()).then((data) => {
    const clocks = setupClocks(data);
    setupMap(data, clocks);
    const render = () => {
      renderSeasonTable(data, metricSelect.value);
      renderAmplitude(data, metricSelect.value, clocks);
      renderSeries(data, metricSelect.value);
    };
    metricSelect.addEventListener("change", render);
    clocks.onChange((slot) => slot !== "single" && renderAmplitude(data, metricSelect.value, clocks));
    render();
  });

  // ------------------------------------------------------------------ season clocks
  // One shared state: "single" for the one-season map, "a" and "b" for every comparison
  // (the map and the bar chart show the same pair, whichever clock is turned).
  function setupClocks(data) {
    const ov = data.overlays;
    const seasons = ov.seasons.map((s) => ({ year: +s.key.slice(0, 4), season: s.key.slice(5) }));
    const latest = seasons[seasons.length - 1];
    const year = ov.diffs.length ? ov.diffs[ov.diffs.length - 1] : null;
    return SeasonClocks(ov.seasons, ov.dates, {
      single: latest,
      a: year ? { year, season: "winter" } : seasons[Math.max(seasons.length - 2, 0)],
      b: year ? { year, season: "summer" } : latest,
    });
  }

  function comparePanel(host, clocks) {
    clocks.clock(host, "a", "A");
    const swap = document.createElement("button");
    swap.type = "button";
    swap.className = "clock-swap";
    swap.textContent = "⇄";
    swap.title = "Inverser A et B";
    swap.setAttribute("aria-label", "Inverser A et B");
    swap.addEventListener("click", () => clocks.swap("a", "b"));
    host.appendChild(swap);
    clocks.clock(host, "b", "B");
  }

  // ------------------------------------------------------------------ map
  function setupMap(data, clocks) {
    const map = L.map("map", { scrollWheelZoom: false, zoomSnap: 0.25 });
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(map);
    const outline = L.geoJSON(data.geometry, { style: { color: "#1a1a19", weight: 2, fill: false }  /* the base map is light in both themes */ }).addTo(map);
    map.fitBounds(outline.getBounds(), { padding: [10, 10] });

    const kind = document.getElementById("layer-kind");
    const dateSelect = document.getElementById("layer-date");
    const dateWrap = document.getElementById("date-wrap");
    const opacity = document.getElementById("opacity");
    const legend = document.getElementById("legend");
    const ov = data.overlays;
    let overlay = null, latest = null;

    dateSelect.innerHTML = ov.dates.map((d) => `<option value="${d}">${new Date(d + "T12:00:00").toLocaleDateString("fr-FR")}</option>`).join("");
    dateSelect.value = ov.dates[ov.dates.length - 1];
    if (ov.seasons.length < 2) {
      kind.querySelector('option[value="compare"]').disabled = true;
      kind.value = "season";
    }

    // the clock panel next to the map: one clock, two clocks or none, depending on the layer
    const panel = document.getElementById("map-clocks");
    const single = document.createElement("div");
    single.className = "clocks__group";
    clocks.clock(single, "single");
    const pair = document.createElement("div");
    pair.className = "clocks__group clocks__group--pair";
    comparePanel(pair, clocks);
    panel.append(single, pair);

    function layout() {
      single.hidden = kind.value !== "season";
      pair.hidden = kind.value !== "compare";
      panel.hidden = single.hidden && pair.hidden;
      dateWrap.hidden = kind.value !== "date";
      map.invalidateSize();
    }

    function url() {
      const st = clocks.state;
      switch (kind.value) {
        case "compare": return config.urls.diff.replace("{a}", clocks.keyOf(st.a)).replace("{b}", clocks.keyOf(st.b));
        case "season": return config.urls.season.replace("{key}", clocks.keyOf(st.single));
        case "date": return config.urls.date.replace("{key}", dateSelect.value);
        default: return config.urls.categories;
      }
    }

    function show() {
      const next = L.imageOverlay(url(), data.bounds, { opacity: +opacity.value, className: "pixelated" });
      latest = next;
      // remove the old image only once the new one is there (turning the clock does not flash the
      // base map); an image overtaken by a newer request while loading removes itself
      next.once("error", () => {
        if (next === latest) legend.insertAdjacentHTML("afterbegin", '<span class="legend__warn" role="status">Image non disponible pour le moment (trop de demandes) : réessayez plus tard.</span>');
      });
      next.once("load error", () => {
        if (next !== latest) return map.removeLayer(next);
        if (overlay) map.removeLayer(overlay);
        overlay = next;
        outline.bringToFront();
      });
      next.addTo(map);
      renderLegend();
    }

    function renderLegend() {
      if (kind.value === "categories") {
        legend.innerHTML = ["trees", "parks", "other", "agri"].map((k) => {
          const c = data.categories.find((x) => x.key === k);
          return `<span class="legend__item"><span class="swatch swatch--${k}"></span>${c.label}</span>`;
        }).join("") + '<span class="legend__item muted">transparent : reste de la commune</span>';
        return;
      }
      const isDiff = kind.value === "compare";
      const [lo, hi] = config.ranges[isDiff ? "diff" : "ndvi"];
      const stops = config.ramps[isDiff ? "diff" : "ndvi"];
      const n = (v) => (v > 0 && isDiff ? "+" : "") + nf(1).format(v);
      const a = clocks.nameOf(clocks.state.a), b = clocks.nameOf(clocks.state.b);
      legend.innerHTML = `
        <span>${isDiff ? `NDVI ${b} − ${a}` : "NDVI"}</span>
        <span><span class="legend__bar" style="display:block;background:linear-gradient(to right, ${stops})"></span>
        <span class="legend__scale"><span>${n(lo)}</span><span>${isDiff ? "0" : n((lo + hi) / 2)}</span><span>${n(hi)}</span></span></span>
        <span class="muted">${isDiff ? `vert : plus vert en ${b} (B) · orange : plus vert en ${a} (A)` : "clair : sol nu, bâti · foncé : végétation dense"}</span>`;
    }

    kind.addEventListener("change", () => { layout(); show(); });
    dateSelect.addEventListener("change", show);
    clocks.onChange((slot) => {
      if ((slot === "single" && kind.value === "season") || (slot !== "single" && kind.value === "compare")) show();
    });
    opacity.addEventListener("input", () => [overlay, latest].forEach((o) => o && o.setOpacity(+opacity.value)));
    document.querySelectorAll(".show-date").forEach((link) => link.addEventListener("click", () => {
      kind.value = "date"; dateSelect.value = link.dataset.date; layout(); show();
    }));
    layout();
    show();
  }

  // ------------------------------------------------------------------ season table
  function renderSeasonTable(data, metric) {
    const table = document.getElementById("season-table");
    const stops = config.ramps.ndvi;
    const [lo, hi] = METRICS[metric].range;
    const head = data.seasons.map((s) => `<th scope="col">${s.label}<small>${s.scenes} date${s.scenes > 1 ? "s" : ""}</small></th>`).join("");
    const rows = data.categories.map((c) => {
      const cells = data.seasons.map((s) => {
        const v = s.values[c.key][metric];
        if (v === null) return '<td class="empty">–</td>';
        const color = rampColor(stops, (v - lo) / (hi - lo));
        return `<td class="${color.dark ? "dark" : ""}" style="background:${color.css}">${fmt(v, metric)}</td>`;
      }).join("");
      return `<tr><th scope="row"><span class="swatch swatch--${c.key}"></span>${c.label}</th>${cells}</tr>`;
    }).join("");
    table.innerHTML = `<thead><tr><th scope="col">Catégorie</th>${head}</tr></thead><tbody>${rows}</tbody>`;
  }

  // ------------------------------------------------------------------ difference bars (B − A)
  let ampChart = null;
  let ampClocks = false;
  const SHORT_LABELS = { all: "Commune", trees: "Arbres", parks: "Parcs", other: "Autres verts", agri: "Agricole", unmapped: "Reste" };
  function renderAmplitude(data, metric, clocks) {
    const empty = data.seasons.length < 2;
    document.getElementById("amp-empty").hidden = !empty;
    document.querySelector(".compare").hidden = empty;
    if (empty) return;
    if (!ampClocks) {
      comparePanel(document.getElementById("amp-clocks"), clocks);
      ampClocks = true;
    }
    const { a, b } = clocks.state;
    const sa = data.seasons.find((s) => s.key === clocks.keyOf(a));
    const sb = data.seasons.find((s) => s.key === clocks.keyOf(b));
    const diff = (key) => {
      const x = sa ? sa.values[key][metric] : null, y = sb ? sb.values[key][metric] : null;
      return x === null || y === null ? null : y - x;
    };
    const year = { label: `${clocks.nameOf(b)} − ${clocks.nameOf(a)}`,
                   values: Object.fromEntries(data.categories.map((c) => [c.key, { [metric]: diff(c.key) }])) };
    document.getElementById("amp-title").textContent = `Écart ${year.label}`;
    const cats = data.categories;
    const values = cats.map((c) => year.values[c.key][metric]);
    const scale = METRICS[metric].percent ? 100 : 1;
    const narrow = window.innerWidth < 600;
    const cfg = {
      type: "bar",
      data: {
        labels: cats.map((c) => (narrow ? SHORT_LABELS[c.key] : c.label)),
        datasets: [{
          data: values.map((v) => (v === null ? null : v * scale)),
          backgroundColor: cats.map((c) => catColor(c.key)),
          borderRadius: 4, borderSkipped: "start", barThickness: 18,
        }],
      },
      options: {
        indexAxis: "y", maintainAspectRatio: false, animation: false,
        layout: { padding: { right: 8 } },
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: (ctx) => `${fmtDiff(values[ctx.dataIndex], metric)} (${year.label})` } },
        },
        scales: {
          // headroom on both sides so the value labels never run into the category names
          x: { suggestedMin: Math.min(0, ...values.filter((v) => v !== null)) * scale * 1.5,
               suggestedMax: Math.max(0, ...values.filter((v) => v !== null)) * scale * 1.5,
               grid: { color: css("--grid") }, border: { color: css("--axis") }, ticks: { color: css("--muted"),
               callback: (v) => (v > 0 ? "+" : "") + nf(METRICS[metric].percent ? 0 : 2).format(v) } },
          y: { grid: { display: false }, border: { color: css("--axis") }, ticks: { color: css("--ink-2") } },
        },
      },
      plugins: [{
        id: "valueLabels",
        afterDatasetsDraw(chart) {
          const { ctx } = chart;
          ctx.save();
          ctx.fillStyle = css("--ink-2");
          ctx.font = "12px system-ui, sans-serif";
          ctx.textBaseline = "middle";
          chart.getDatasetMeta(0).data.forEach((bar, i) => {
            if (values[i] === null) return;
            const right = values[i] >= 0;
            ctx.textAlign = right ? "left" : "right";
            ctx.fillText(fmtDiff(values[i], metric), bar.x + (right ? 6 : -6), bar.y);
          });
          ctx.restore();
        },
      }],
    };
    if (ampChart) ampChart.destroy();
    ampChart = new Chart(document.getElementById("amp-chart"), cfg);
  }

  // ------------------------------------------------------------------ time series
  let seriesChart = null;
  // secondary encoding: every category also has its own marker shape (filled shapes only)
  const POINT_STYLES = ["circle", "rect", "triangle", "rectRot", "rectRounded", "triangle"];
  const POINT_ROTATION = [0, 0, 0, 0, 0, 180];
  function renderSeries(data, metric) {
    const scale = METRICS[metric].percent ? 100 : 1;
    const datasets = data.categories.map((c, i) => ({
      label: c.label,
      data: data.series.map((s) => ({ x: s.date, y: s.values[c.key] && s.values[c.key][metric] !== null ? s.values[c.key][metric] * scale : null })),
      borderColor: catColor(c.key),
      backgroundColor: catColor(c.key),
      borderWidth: c.key === "all" ? 3 : 2,
      pointStyle: POINT_STYLES[i],
      pointRotation: POINT_ROTATION[i],
      pointRadius: 3.5, pointHoverRadius: 6,
      pointBorderColor: css("--surface"), pointBorderWidth: 1,
      tension: 0.25, spanGaps: true,
    }));
    const cfg = {
      type: "line",
      data: { datasets },
      options: {
        maintainAspectRatio: false, animation: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { position: "bottom", labels: { usePointStyle: true, color: css("--ink-2"), boxWidth: 8, padding: 14 } },
          tooltip: {
            itemSort: (a, b) => b.parsed.y - a.parsed.y,
            callbacks: {
              title: (items) => {
                const s = data.series[items[0].dataIndex];
                return `${new Date(s.date + "T12:00:00").toLocaleDateString("fr-FR", { dateStyle: "long" })} · ${s.season}`;
              },
              label: (ctx) => ` ${ctx.dataset.label} : ${fmt(ctx.parsed.y / scale, metric)}`,
            },
          },
        },
        scales: {
          x: { type: "time", time: { unit: "month", tooltipFormat: "dd/MM/yyyy", displayFormats: { month: "MMM yy" } },
               grid: { color: css("--grid") }, border: { color: css("--axis") }, ticks: { color: css("--muted"), maxRotation: 0, autoSkipPadding: 12,
                 callback: (v) => new Date(v).toLocaleDateString("fr-FR", { month: "short", year: "2-digit" }) } },
          y: { suggestedMin: 0, suggestedMax: METRICS[metric].percent ? 100 : 0.8,
               grid: { color: css("--grid") }, border: { display: false },
               ticks: { color: css("--muted"), callback: (v) => (METRICS[metric].percent ? v + " %" : nf(1).format(v)) },
               title: { display: true, text: METRICS[metric].label, color: css("--muted") } },
        },
      },
    };
    if (seriesChart) seriesChart.destroy();
    seriesChart = new Chart(document.getElementById("series-chart"), cfg);
  }
})();
