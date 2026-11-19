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
    setupMap(data);
    const render = () => {
      renderSeasonTable(data, metricSelect.value);
      renderAmplitude(data, metricSelect.value);
      renderSeries(data, metricSelect.value);
    };
    metricSelect.addEventListener("change", render);
    render();
  });

  // ------------------------------------------------------------------ map
  function setupMap(data) {
    const map = L.map("map", { scrollWheelZoom: false, zoomSnap: 0.25 });
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(map);
    const outline = L.geoJSON(data.geometry, { style: { color: "#1a1a19", weight: 2, fill: false }  /* the base map is light in both themes */ }).addTo(map);
    map.fitBounds(outline.getBounds(), { padding: [10, 10] });

    const kind = document.getElementById("layer-kind");
    const choice = document.getElementById("layer-choice");
    const choiceWrap = document.getElementById("layer-choice-wrap");
    const choiceLabel = document.getElementById("layer-choice-label");
    const opacity = document.getElementById("opacity");
    const legend = document.getElementById("legend");
    const ov = data.overlays;
    let overlay = null;

    const options = {
      diff: { label: "Année", items: ov.diffs.map((y) => [String(y), `Été ${y} − hiver ${y}`]) },
      season: { label: "Saison", items: ov.seasons.map((s) => [s.key, `${s.label} (${s.count} date${s.count > 1 ? "s" : ""})`]) },
      date: { label: "Date", items: ov.dates.map((d) => [d, new Date(d + "T12:00:00").toLocaleDateString("fr-FR")]) },
      categories: { label: "", items: [] },
    };
    // no full year yet: start on the seasonal view
    if (!ov.diffs.length) {
      kind.querySelector('option[value="diff"]').disabled = true;
      kind.value = "season";
    }

    function fillChoices() {
      const o = options[kind.value];
      choiceWrap.hidden = !o.items.length;
      choiceLabel.textContent = o.label;
      choice.innerHTML = o.items.map(([v, l]) => `<option value="${v}">${l}</option>`).join("");
      if (o.items.length) choice.value = o.items[o.items.length - 1][0];
    }

    function show() {
      if (overlay) map.removeLayer(overlay);
      const url = kind.value === "categories" ? config.urls.categories : config.urls[kind.value].replace("{key}", choice.value);
      overlay = L.imageOverlay(url, data.bounds, { opacity: +opacity.value, className: "pixelated" }).addTo(map);
      outline.bringToFront();
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
      const isDiff = kind.value === "diff";
      const [lo, hi] = config.ranges[isDiff ? "diff" : "ndvi"];
      const stops = config.ramps[isDiff ? "diff" : "ndvi"];
      const n = (v) => (v > 0 && isDiff ? "+" : "") + nf(1).format(v);
      legend.innerHTML = `
        <span>${isDiff ? "NDVI été − hiver" : "NDVI"}</span>
        <span><span class="legend__bar" style="display:block;background:linear-gradient(to right, ${stops})"></span>
        <span class="legend__scale"><span>${n(lo)}</span><span>${isDiff ? "0" : n((lo + hi) / 2)}</span><span>${n(hi)}</span></span></span>
        <span class="muted">${isDiff ? "vert : plus vert en été · orange : plus vert en hiver" : "clair : sol nu, bâti · foncé : végétation dense"}</span>`;
    }

    kind.addEventListener("change", () => { fillChoices(); show(); });
    choice.addEventListener("change", show);
    opacity.addEventListener("input", () => overlay && overlay.setOpacity(+opacity.value));
    document.querySelectorAll(".show-date").forEach((a) => a.addEventListener("click", () => {
      kind.value = "date"; fillChoices(); choice.value = a.dataset.date; show();
    }));
    fillChoices();
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

  // ------------------------------------------------------------------ amplitude bars
  let ampChart = null;
  const SHORT_LABELS = { all: "Commune", trees: "Arbres", parks: "Parcs", other: "Autres verts", agri: "Agricole", unmapped: "Reste" };
  const ampYear = document.getElementById("amp-year");
  function renderAmplitude(data, metric) {
    const empty = !data.amplitude.length;
    document.getElementById("amp-empty").hidden = !empty;
    document.getElementById("amp-chart").parentElement.hidden = empty;
    document.getElementById("amp-year-wrap").hidden = empty;
    if (empty) return;
    if (!ampYear.options.length) {
      ampYear.innerHTML = data.amplitude.map((a) => `<option value="${a.year}">${a.year}</option>`).join("");
      ampYear.value = data.amplitude[data.amplitude.length - 1].year;
      ampYear.addEventListener("change", () => renderAmplitude(data, metricSelect.value));
    }
    const year = data.amplitude.find((a) => String(a.year) === ampYear.value);
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
          tooltip: { callbacks: { label: (ctx) => `${fmtDiff(values[ctx.dataIndex], metric)} (été ${year.year} − hiver ${year.year})` } },
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
