# Saudi Arabia Weather Contour Viewer

An interactive web map that draws weather contours over Saudi Arabia, for the next 48 hours or for any historical period, with province and governorate boundaries on top.

## Features

- **Five variables:** rainfall, temperature, humidity, wind speed and wind direction.
- **Forecast mode:** an hour-by-hour slider across the next 48 hours.
- **Historical mode:** pick a date range or a season (spring, summer, autumn, winter) and view the aggregated field from ERA5 / ERA5-Land reanalysis.
- **Point query:** click anywhere to get the time series for that location.
- **Boundaries:** province and governorate layers from GeoJSON, cached in memory for fast loading. The boundary files are **not included** in this repository (see below).
- **Smooth contours:** values are sampled on a coarse grid, upscaled and Gaussian-smoothed, then rendered as a transparent overlay on a Leaflet map (street or satellite base layer).
- A background thread warms the cache so the map responds quickly.

## Run it

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python app.py
```

Then open http://localhost:5001. Weather data comes live from the Open-Meteo forecast and archive APIs, so an internet connection is needed. Set `FLASK_DEBUG=1` for development.

**Boundary layers:** to show province and governorate outlines, place your own files at `data/geo/ksa_provinces_simplified.geojson` and `data/geo/ksa_governorates_simplified.geojson`. Without them the map runs normally, just without the boundary overlay.

## Project layout

```
app.py              Flask backend: Open-Meteo fetching, caching, contour rendering
templates/          map page (Leaflet)
static/             CSS and boundary-layer JavaScript
data/geo/           boundary GeoJSON goes here (not included)
```

**Tech:** Python · Flask · NumPy · SciPy · Matplotlib · Leaflet · Open-Meteo API

**Author:** Ritaj Albogami
