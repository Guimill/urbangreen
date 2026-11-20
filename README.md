# Végétation urbaine · Sentinel-2

A Django app that follows the vegetation of French communes through the seasons with free
Sentinel-2 satellite images, and splits it into **trees**, **parks**, **other green spaces**
(lawns, cemeteries, sports grounds…), **farmland** and **the rest of the town**.

Live: https://urbangreen.mi-fan.fr · Example: Angers (INSEE 49007), December 2024 → November 2026.

---

## 1. How it works, in plain words

### What the satellite measures

Sentinel-2 (ESA, Copernicus) photographs every place in France every 2 to 5 days, with 10 m
pixels. Among its 13 bands, two matter here:

- **red** (B04): leaves absorb it, chlorophyll uses it for photosynthesis;
- **near infrared** (B08): healthy leaves reflect it strongly (it bounces in their cells).

The **NDVI** (Normalised Difference Vegetation Index) combines them:

```
NDVI = (NIR − red) / (NIR + red)
```

| NDVI | What it usually is |
|---|---|
| < 0 | water |
| 0 – 0.2 | roofs, roads, bare soil |
| 0.2 – 0.3 | very sparse or dry vegetation |
| 0.3 – 0.6 | grass, mixed pixels, young crops — the app counts ≥ 0.3 as **vegetated** |
| ≥ 0.6 | dense, healthy vegetation (full canopy, irrigated lawn) — counted as **dense** |

One pixel is a 10 m × 10 m square: what it "sees" is the **average** of everything in it.
Keep that in mind, it explains most of the surprises (see section 3).

### What the app does for a commune

1. **Boundary** — the official outline comes from geo.api.gouv.fr.
2. **Green spaces** — OpenStreetMap is queried (Overpass API) and every 10 m pixel gets one
   category. When objects overlap, priority is trees → parks → other green → farmland.
   Isolated trees (OSM points) are drawn as a 4 m-radius crown, tree rows as an 8 m-wide band,
   and every pixel they *touch* is counted as "trees" (otherwise most street trees would vanish).
3. **Images** — the Element 84 *Earth Search* catalogue (free, no account) is searched for
   Sentinel-2 L2A images (surface reflectance, already corrected for the atmosphere). Dates whose
   tiles only cover part of the commune are dropped, then the least cloudy dates of each month are
   kept (3 per month by default).
4. **Reading** — only the commune window is read from the cloud-optimised GeoTIFFs (a few MB
   instead of ~1 GB per tile). Bands red, NIR and **SCL** (ESA's scene classification) are
   reprojected on one grid (Web Mercator, 10 m on the ground) so that every date lines up exactly.
5. **Masking** — SCL tells which pixels are cloud, cloud shadow, snow or no data: they are
   removed. A date is kept only if ≥ 60 % of the commune is visible. Water is left out of the means.
6. **Statistics per category and per date** — mean NDVI, vegetated share (NDVI ≥ 0.3), dense
   share (NDVI ≥ 0.6). The NDVI raster of each date is stored (compressed) for the maps.
7. **Seasons** — meteorological seasons. **Winter 2026 = December 2025 + January + February
   2026.** A season's value is the **median** of its dates, so one hazy date does not move it;
   a season map is the per-pixel median of its dates.

Analysed dates are never downloaded again: re-running an analysis over a longer period only
fetches the new dates.

### Reading the commune page

- **Key figures** — vegetated share in the latest summer and winter, and the summer − winter
  change of the whole commune's NDVI.
- **Map** — choose a layer:
  - *Comparer deux saisons*: two **season clocks** A and B; the map shows NDVI(B) − NDVI(A).
    Green: greener in B. Orange: greener in A. Grey: no change.
  - *Une saison*: one clock; the map shows that season's median NDVI.
  - *Une date*: one cloud-free image.
  - *Catégories*: what OpenStreetMap says each pixel is.
- **Season clock** — the face is a year: December at 12 o'clock, one month per "hour", so winter
  is around 1 o'clock, spring 4, summer 7, autumn 10. Click a quarter to choose the season, use
  ‹ › to change the year, ⇄ to swap A and B. The dots on the rim are the cloud-free dates
  available that year; a dashed quarter has no image. With the keyboard, ← → walk through the
  available seasons in time order.
  The same A and B drive the map *and* the bar chart, so you can compare winter → summer of one
  year (the seasonal effect) or summer 2025 → summer 2026 (the effect of a drier year).
- **Saisons table** — every season × category, coloured from light (low) to dark green (high).
  The *Indicateur* menu switches between vegetated share, mean NDVI and dense share.
- **Écart B − A** — one bar per category: right = greener in B, left = greener in A.
- **Évolution date par date** — every date, every category.
- **Dates** — the list of dates with the visible share and why some were rejected.

---

## 2. Running it

### On your PC

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python manage.py migrate
.\.venv\Scripts\python manage.py runserver 8010
```

Open http://127.0.0.1:8010, search a commune, **Suivre**, then **Analyser**. Locally anyone can
analyse; on the server only an admin can (see below).

Command line (handy for a cron job):

```powershell
.\.venv\Scripts\python manage.py monitor_city Angers                       # Dec. two years back → today
.\.venv\Scripts\python manage.py monitor_city 49007 --start 2025-01-01 --end 2025-12-31 --per-month 4
.\.venv\Scripts\python manage.py monitor_city Nantes --refresh-osm          # re-download OSM green spaces
.\.venv\Scripts\python manage.py diagnose_trees Angers --output docs/arbres-angers.md   # section 3
.\.venv\Scripts\python manage.py monitor_heat Angers                       # heat islands, 10 years (section 4)
.\.venv\Scripts\python manage.py refresh_all                              # every commune, both analyses (server cron, every 3 months)
.\.venv\Scripts\python manage.py test monitor                              # 44 tests, no network
```

A commune of ~50 km² over two years takes about 10 minutes and 15 MB.

### On the server (urbangreen.mi-fan.fr)

Same pattern as the other sites on the IONOS VPS: a Docker container behind Nginx Proxy Manager.

- Code: `/srv/urbangreen`, a checkout of GitHub `Guimill/urbangreen`.
- Container `urbangreen-web-1` (gunicorn, one worker, network alias `urbangreen:8000`).
- Data: Docker volume `urbangreen_data` (SQLite + rasters), kept across updates.
- Settings: `/srv/urbangreen/.env` (not in Git): `SECRET_KEY`, `DEBUG=0`, `ALLOWED_HOSTS`,
  `CSRF_TRUSTED_ORIGINS`, `WRITE_REQUIRES_LOGIN=1`.

**Deploy a change** (tests → push → server rebuild):

```powershell
.\deploy\deploy.ps1
```

**Admin** — visitors can browse everything; adding a commune or launching an analysis needs a
staff login (`/admin/`), because every analysis downloads imagery for several minutes. Set your
own password with:

```powershell
ssh -t -i "$HOME\.ssh\cv_deploy_ed25519" root@194.164.199.244 "cd /srv/urbangreen && docker compose exec web python manage.py changepassword admin"
```

**Where the data lives, and what visitors can make the server do**

Everything is on the server, in the Docker volume `urbangreen_data` (about 100 MB for Angers +
Nantes): the database, one compressed NDVI raster per date, and a `cache/` folder of seasonal
composites and map images. Nothing is fetched from outside when a visitor browses.

| Action | Who | Cost |
|---|---|---|
| Search a commune (calls geo.api.gouv.fr) | admins only — hidden for visitors | — |
| Add a commune, launch an analysis (downloads Sentinel-2) | admins only | minutes |
| Open a page, the data, an image already computed | everyone | read from disk |
| First display of a season, a comparison or a date | visitors: 40 new images per hour per address, then HTTP 429; admins: unlimited | ~0.2–3 s of CPU, once |

Images are built one at a time and the container is capped at **1 CPU and 1.5 GB of RAM**
(`compose.yaml`), so even a visitor clicking through every pair of seasons cannot slow down the
other sites of the server. The number of possible images is finite (seasons² + dates per
commune, a few hundred KB each) and each is computed only once. The hourly allowance is set by
`VISITOR_BUILDS_PER_HOUR` in `.env`.


---

## 3. Why can trees look greener in winter than in summer?

### The short answer

Because most pixels labelled "trees" in a town are **not mostly tree**.

A street tree is mapped in OpenStreetMap as a point. Its crown (≈ 4 m radius, **≈ 50 m²**) falls
in a 10 m pixel (**100 m²**) and often straddles two or more of them. Each of those pixels
then measures a mix of a bit of crown and a lot of lawn, pavement or roof:

```
NDVI(pixel) ≈ f · NDVI(crown) + (1 − f) · NDVI(surroundings)        f = share of the pixel under the crown
```

Around Angers (oceanic climate):

- a **deciduous crown** is bare in winter and full in summer → NDVI goes **up** in summer;
- the **lawns around it** are green in the mild, wet winter and **yellow in a dry summer** → NDVI
  goes **down**, by a lot (−0.18 for the "other green spaces" of Angers in 2026).

When the crown covers less than about a quarter of the pixel, the lawn wins and the pixel is
greener in winter. Three smaller effects add to it: **evergreen trees** (conifers, holm oaks,
ivy) stay green in winter; **summer drought** stresses the trees themselves (their NDVI peaks in
May and falls through July–August); and the summer 2026 was drier than 2025.

So "trees greener in winter" is not a satellite error: it is the lawn under and around urban
trees showing through.

### The evidence (Angers, winter 2026 → summer 2026)

Produced by `python manage.py diagnose_trees Angers`; full report in
[`docs/arbres-angers-2026.md`](docs/arbres-angers-2026.md).

As a whole, the "trees" category is a little greener in summer (NDVI 0.51 → 0.58), but
**33 % of its pixels are greener in winter**, and the tests below show which ones and why.

**Test 1: by kind of tree object.** Pure canopy against isolated trees.

| OSM objects | Area | NDVI winter | NDVI summer | Summer − winter | Pixels greener in winter |
|---|---:|---:|---:|---:|---:|
| Core of woods (> 20 m from the edge) | 74 ha | 0.61 | 0.80 | **+0.19** | 12 % |
| Wood edges | 160 ha | 0.60 | 0.73 | +0.13 | 21 % |
| Tree rows | 121 ha | 0.46 | 0.53 | +0.07 | 34 % |
| Isolated trees | 193 ha | 0.42 | 0.40 | **−0.02** | **51 %** |

Where the pixel really is canopy, trees behave as expected. Isolated trees, a third of the
"trees" area, do not.

**Test 2: by canopy fraction.** The share of each pixel under a mapped crown, measured on
a 2 m sub-grid.

| Crown in the pixel | Pixels | Summer − winter | Pixels greener in winter |
|---|---:|---:|---:|
| 0–25 % | 16 109 | −0.01 | 50 % |
| 25–50 % | 7 893 | +0.01 | 44 % |
| 50–75 % | 7 699 | +0.06 | 36 % |
| 75–100 % | 6 322 | +0.10 | 27 % |
| 100 % | 16 872 | +0.18 | 13 % |

The more crown in the pixel, the greener it gets in summer, steadily: a dose–response, the
signature of mixed pixels.

**Test 3: mixing model.** The formula above fitted on every tree pixel gives a pure crown
**+0.16** and surroundings **−0.04**. A pixel turns greener in summer once the crown covers more
than **22 %** of it. The surroundings move less than lawns (−0.18) because they also include
roads and roofs, which do not change.

**Test 4: leaf type.** Only for trees where OSM records it.

| | Area | Summer − winter | Pixels greener in winter |
|---|---:|---:|---:|
| Broadleaved | 128 ha | +0.08 | 31 % |
| Deciduous | 26 ha | +0.13 | 25 % |
| Needleleaved (conifers) | 2.6 ha | −0.12 | 83 % |
| Evergreen | 0.9 ha | −0.09 | 84 % |

Evergreen trees really are greener in winter, but in Angers they are too few in OSM to weigh
on the category.

**Test 5: weather.** Open-Meteo / ERA5, water balance = rain − reference evapotranspiration.

| | Rain | Evapotranspiration | Balance | Mean max. temp. |
|---|---:|---:|---:|---:|
| Winter 2026 | 271 mm | 70 mm | +201 mm | 10.5 °C |
| Summer 2025 | 120 mm | 451 mm | −331 mm | 26.8 °C |
| Summer 2026 | 115 mm | 524 mm | **−408 mm** | 29.6 °C |

The ground is wet in winter and dry in summer, and 2026 was drier and hotter than 2025, as
summer 2026 NDVI shows for every kind of tree (season table in the report).

**Synthetic check (unit tests).** `monitor/tests/test_trees.py` builds a fake town where the
hypothesis holds exactly: crown 0.35 → 0.85, lawn 0.65 → 0.25, each pixel mixed by its canopy
fraction. The diagnosis must then find the wood core greener in summer, isolated trees greener
in winter, a change that grows with the canopy fraction, and a mixing model that recovers the
crown and lawn values and the break-even fraction 0.4 / 0.9 = 44 %. A control case where the
lawn does not change must give **no** pixel greener in winter. All pass.

### Limits

- Winter 2026 has only **two dates** (both in December): January and February were cloudy.
  December still has some leaves and active grass; a clear January would show bare trees better.
- OSM is incomplete: unmapped trees fall in "the rest", and leaf type is rarely recorded.
- A 4 m radius is an average crown; old plane trees are much wider, young trees narrower.
- The low winter sun casts long shadows of buildings. Shadowed pixels keep a fairly normal NDVI
  (it is a ratio), but they are noisier. This effect is not tested here.

### What to do with it

- For tree health, look at the **core of the woods** (or pixels with ≥ 75 % canopy). For
  "is the city green", use the whole category.
- Compare **summer with summer** (clock A = Été 2025, B = Été 2026) to see drought rather than
  seasons.

---

## 4. Heat islands

The **Îlots de chaleur** section of a commune page cuts the commune into **200 m squares** and
compares the temperature of each square with the rest of the commune, or with the same season
in other years.

### Which temperature

There is no free source of *air* temperature at 200 m. Weather reanalyses (ERA5, served by
Open-Meteo) give one value per 9–25 km, so one value for the whole city, and Météo-France
stations are a handful of points. What satellites do measure finely is the **surface
temperature**: the thermal band of **Landsat 8 and 9** (100 m native, delivered at 30 m), taken
at about 12:50 summer time, every 8 days at best.

Surface temperature is not air temperature: in summer a car park can be at 45 °C under 28 °C
air. But its differences between neighbourhoods have the same causes (mineral surfaces, few
trees, no water), which is why it is the standard way of mapping urban heat islands (*surface
urban heat island*, SUHI).

Air temperature is still used, as context. For every image date the app stores that day's
maximum air temperature at the city centre and the **1991-2020 normal** of that calendar day,
both from Open-Meteo (ERA5). That says whether an image was taken on a heat-wave day or an
ordinary one.

### The three comparisons

| Choice on the page | Value of a square | Answers |
|---|---|---|
| *à la moyenne de la commune* | for each date, square − mean of the commune that day; then the median over the dates of the season | where are the heat islands? The day's weather cancels out: a hot day and a mild day give the same map. |
| *à la même saison des autres années* | median temperature of the season − median of the same season in all other downloaded years | was this summer hotter than usual here, and where did it change (new buildings, felled or planted trees)? Season only. |
| *à rien* | median surface temperature (°C) | raw values |

The **season clock** picks the season; *Période → Une date* shows a single image. The tiles
under the map give:
- the hot/cold contrast (90th − 10th percentile of the squares);
- the air temperature of the image days against the normal;
- the **effect of vegetation**: slope of temperature against NDVI.

### Vegetation and heat

The scatter plot puts every square by its vegetation (x) against its temperature (y). Vegetation
can be:
- the Sentinel-2 NDVI of the same season (this includes private gardens, which OpenStreetMap does
  not map);
- the share of OSM trees in the square;
- the share of all OSM green spaces in the square.

First results for Angers, summer 2025 (10 dates) and summer 2026 (14 dates):

| | Summer 2025 | Summer 2026 |
|---|---:|---:|
| Contrast, hottest 10 % − coolest 10 % of squares | 10.1 °C | 7.9 °C |
| Effect of +0.1 NDVI on a square | −2.1 °C (r² 0.70) | −1.9 °C (r² 0.63) |
| Air max. on image days / 1991-2020 normal | 29.9 / 23.6 °C | 30.9 / 24.3 °C |

Summer 2022 comes out at +2.5 °C (median square) over the other summers.

### Limits

- **Clear-sky bias.** Landsat sees only cloud-free days, which are hotter than average (about
  +6 °C of air at Angers in summer). The "normal" of a square is therefore the normal of
  *sunny* days of that season. The comparison with the city mean is not affected.
- **One time of day.** Surface heat at about 12:50 is not night-time heat, when heat islands
  hurt health most. Night-time thermal images (ECOSTRESS, Landsat night passes) are rare and not
  used here.
- **Resolution.** 100 m native, so a 200 m square averages a few true pixels. Single streets
  are not resolved, but blocks and neighbourhoods are.
- **Water** pixels (Landsat QA) are left out: a river is cool for reasons unrelated to the urban
  fabric. A square that is mostly water has no value.

### Cost

Searching and reading uses Microsoft Planetary Computer (free, anonymous; URLs are signed with a
temporary token that the app fetches by itself). Each date takes about 1 s and a few KB on disk:
`heat/<date>.npz` holds one float16 per square. Ten years for Angers came to 305 dates, of which
212 were kept, in a few minutes. The 30-year normals are downloaded once per commune, to
`heat/normals.json`.

---

## 5. Reference

**Data and storage**

- `data/db.sqlite3`: communes, dates (`Scene`), statistics per category (`Observation`).
- `data/rasters/<INSEE>/`: `masks.npz` (commune + categories), `ndvi/<date>.npz` (NDVI × 100 in
  int8), `heat/<date>.npz` (surface temperature per 200 m square), `heat/normals.json` (air
  normals) and `cache/` (map PNGs and heat layers, rebuilt automatically).
- `HeatScene`: one Landsat date, with the mean surface temperature, the day's maximum air
  temperature and its normal.

**Settings (environment variables)**: `SECRET_KEY`, `DEBUG` (1/0), `ALLOWED_HOSTS`,
`CSRF_TRUSTED_ORIGINS`, `WRITE_REQUIRES_LOGIN` (1 = only staff can add/analyse), `DATA_DIR`,
`STAC_URL`, `STAC_COLLECTION`, `HEAT_STAC_URL`, `HEAT_COLLECTION`, `HEAT_SIGN_URL`.

**Code**

| File | Role |
|---|---|
| `monitor/services/communes.py` | geo.api.gouv.fr |
| `monitor/services/osm.py` | Overpass query, OSM element → geometry → category |
| `monitor/services/stac.py` | Sentinel-2 search, grouping by date, choice of dates |
| `monitor/services/raster.py` | grid, reading, NDVI, statistics, map images |
| `monitor/services/pipeline.py` | chains everything (command and *Analyser* button) |
| `monitor/services/analysis.py` | seasonal medians and composites for the pages |
| `monitor/services/trees.py` | the "trees in winter" diagnosis (section 3) |
| `monitor/services/heat.py` | Landsat surface temperature, 200 m squares, air normals, map layers (section 4) |
| `monitor/static/monitor/clock.js` | the season clock |
| `monitor/static/monitor/city.js` | map, table and charts of the commune page |
| `monitor/static/monitor/heat.js` | heat island map, key figures and vegetation/heat chart |

Data: Copernicus Sentinel-2 (ESA) · Landsat 8/9 (USGS/NASA, via Microsoft Planetary Computer) ·
© OpenStreetMap contributors (ODbL) · geo.api.gouv.fr ·
weather Open-Meteo (CC BY 4.0).
