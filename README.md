# Végétation urbaine · Sentinel-2

Django app that tracks the vegetation of French communes through the seasons using Sentinel-2 imagery.

For each commune it:

1. fetches the official boundary (geo.api.gouv.fr);
2. downloads its green spaces from OpenStreetMap and sorts every 10 m pixel into one of five categories:
   **trees and woods**, **parks and gardens**, **other green spaces** (lawns, cemeteries, allotments, grass pitches),
   **agricultural land**, **the rest** (built-up areas, roads, private gardens);
3. searches the Sentinel-2 L2A images (Element 84 Earth Search, collection 1, free, no account needed)
   and keeps the least cloudy dates of each month;
4. for each date, reads only the commune window (red B04, near infrared B08 and SCL classification) straight from the
   cloud-optimised GeoTIFFs, mosaics several tiles if needed, masks clouds, shadows, snow and water,
   then computes NDVI per category: mean, vegetated share (NDVI ≥ 0.3) and dense share (NDVI ≥ 0.6);
5. aggregates by meteorological season (median of the dates; the winter of year N runs from December N-1 to February)
   and compares summer with winter.

The commune page shows key figures, a map (NDVI per date, seasonal median, summer − winter difference,
OSM categories), a season × category table, the summer − winter difference per category and the date-by-date time series.

## Getting started

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python manage.py migrate
.\.venv\Scripts\python manage.py runserver 8010
```

Then open http://127.0.0.1:8010, search for a commune, click **Suivre** (follow), then **Analyser** (analyse).

From the command line (useful for a cron job: dates already processed are never downloaded again):

```powershell
.\.venv\Scripts\python manage.py monitor_city Angers                 # from December, two years back, until today
.\.venv\Scripts\python manage.py monitor_city 49007 --start 2025-01-01 --end 2025-12-31 --per-month 4
.\.venv\Scripts\python manage.py monitor_city Nantes --refresh-osm    # download the OSM green spaces again
```

Tests: `.\.venv\Scripts\python manage.py test monitor` (no network: OSM, STAC and the images are simulated).

## Data and storage

- `data/db.sqlite3`: communes, dates (`Scene`) and statistics per category (`Observation`).
- `data/rasters/<INSEE>/`: `masks.npz` (commune + categories), `ndvi/<date>.npz` (NDVI quantised to int8)
  and `cache/` (map PNGs, regenerated automatically).
- Order of magnitude: a commune of about 50 km² over two years takes roughly 10 minutes and 15 MB.

All rasters for a commune share one grid in Web Mercator (EPSG:3857) with 10 m ground pixels:
tiles from different UTM zones line up and the overlays drop straight onto Leaflet.

## Settings (environment variables)

`SECRET_KEY`, `DEBUG` (1/0), `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `DATA_DIR`, `STAC_URL`, `STAC_COLLECTION`.

## Code layout

- `monitor/services/communes.py`: geo.api.gouv.fr
- `monitor/services/osm.py`: Overpass query, element → geometry, categories
- `monitor/services/stac.py`: Sentinel-2 search, grouping by date, choosing dates
- `monitor/services/raster.py`: grid, reading, NDVI, statistics, images
- `monitor/services/pipeline.py`: chains it all together (command and "Analyser" button)
- `monitor/services/analysis.py`: seasonal aggregates and composites for the pages

Data: Copernicus Sentinel-2 (ESA), © OpenStreetMap contributors (ODbL), geo.api.gouv.fr.
