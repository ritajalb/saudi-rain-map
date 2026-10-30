from flask import Flask, render_template, send_file, request, jsonify, make_response
import numpy as np
import os
import requests
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import cm
from matplotlib.colors import Normalize, BoundaryNorm
from scipy.ndimage import gaussian_filter, zoom
import io
from datetime import datetime, timedelta
from time import time
from threading import Thread
import json
from pathlib import Path

app = Flask(__name__)

SAUDI_BOUNDS = {
    'lat_min': 16.0,
    'lat_max': 32.0,
    'lon_min': 34.5,
    'lon_max': 56.0
}

# ===== GeoJSON fast loader (in-memory cache) =====
BASE_DIR = Path(__file__).resolve().parent
GEO_DIR = BASE_DIR / "data" / "geo"

_geo_cache = {
    "governorates": None,
    "provinces": None
}

def _load_geojson_cached(name: str) -> dict:
    if _geo_cache.get(name) is not None:
        return _geo_cache[name]

    if name == "governorates":
        path = GEO_DIR / "ksa_governorates_simplified.geojson"
    elif name == "provinces":
        path = GEO_DIR / "ksa_provinces_simplified.geojson"
    else:
        raise ValueError("Invalid geojson name")

    if not path.exists():
        # Boundary files are not shipped with the repo; the map still works without them.
        return {"type": "FeatureCollection", "features": []}

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    _geo_cache[name] = data
    return data

def _json_response(data: dict, cache_seconds: int = 86400):
    resp = make_response(jsonify(data))
    resp.headers["Cache-Control"] = f"public, max-age={cache_seconds}"
    return resp

@app.route("/api/geo/governorates")
def api_geo_governorates():
    data = _load_geojson_cached("governorates")
    return _json_response(data, cache_seconds=86400)

@app.route("/api/geo/provinces")
def api_geo_provinces():
    data = _load_geojson_cached("provinces")
    return _json_response(data, cache_seconds=86400)

# -------------------------
# Cache (TTL)
# -------------------------
class TTLCache:
    def __init__(self, ttl_seconds=600):
        self.ttl = ttl_seconds
        self.store = {}

    def get(self, key):
        item = self.store.get(key)
        if not item:
            return None
        ts, val = item
        if time() - ts > self.ttl:
            self.store.pop(key, None)
            return None
        return val

    def set(self, key, val):
        self.store[key] = (time(), val)

POINT_CACHE = TTLCache(ttl_seconds=600)  # 10 minutes
WINDROSE_CACHE = TTLCache(ttl_seconds=600)

def om_field(variable: str) -> str:
    if variable == "rainfall":
        return "precipitation"
    if variable == "temperature":
        return "temperature_2m"
    if variable == "humidity":
        return "relative_humidity_2m"
    if variable == "wind_speed":
        return "windspeed_10m"
    if variable == "wind_direction":
        return "winddirection_10m"
    return "temperature_2m"

def fetch_point_hour(variable: str, lat: float, lon: float, hour_index: int) -> float:
    key = ("fcst", variable, round(float(lat), 3), round(float(lon), 3), int(hour_index))
    cached = POINT_CACHE.get(key)
    if cached is not None:
        return cached

    url = (
        f"https://api.open-meteo.com/v1/forecast?"
        f"latitude={lat}&longitude={lon}"
        f"&hourly=temperature_2m,relative_humidity_2m,precipitation,windspeed_10m,winddirection_10m"
        f"&forecast_days=2&timezone=UTC"
    )

    defaults = {
        "rainfall": 0.0,
        "temperature": 32.0,
        "humidity": 45.0,
        "wind_speed": 3.0,
        "wind_direction": 0.0
    }

    try:
        r = requests.get(url, timeout=10)
        js = r.json()
        field = om_field(variable)
        if "hourly" in js and field in js["hourly"]:
            arr = js["hourly"][field]
            val = float(arr[hour_index % len(arr)])
        else:
            val = defaults.get(variable, np.nan)
    except Exception:
        val = defaults.get(variable, np.nan)

    POINT_CACHE.set(key, val)
    return val

# -------------------------
# Historical bundle (Open-Meteo Archive)
# -------------------------
def fetch_point_historical_bundle(lat: float, lon: float, start_date: str, end_date: str, model: str = "era5") -> dict:
    key = ("hist_bundle", model, round(float(lat), 3), round(float(lon), 3), start_date, end_date)
    cached = POINT_CACHE.get(key)
    if cached is not None:
        return cached

    url = (
        f"https://archive-api.open-meteo.com/v1/archive?"
        f"latitude={lat}&longitude={lon}"
        f"&start_date={start_date}&end_date={end_date}"
        f"&hourly=temperature_2m,relative_humidity_2m,precipitation,windspeed_10m,winddirection_10m"
        f"&timezone=UTC"
        f"&models={model}"
    )

    try:
        r = requests.get(url, timeout=25)
        js = r.json()
        hourly = js.get("hourly", {})

        out = {
            "time": hourly.get("time", []),
            "temperature": hourly.get("temperature_2m", []),
            "humidity": hourly.get("relative_humidity_2m", []),
            "rainfall": hourly.get("precipitation", []),
            "wind_speed": hourly.get("windspeed_10m", []),
            "wind_direction": hourly.get("winddirection_10m", []),
        }
    except Exception:
        out = {"time": [], "temperature": [], "humidity": [], "rainfall": [], "wind_speed": [], "wind_direction": []}

    POINT_CACHE.set(key, out)
    return out

def safe_mean(arr):
    if not arr:
        return None
    try:
        a = np.array(arr, dtype=float)
        return float(np.nanmean(a))
    except Exception:
        return None

def safe_sum(arr):
    if not arr:
        return None
    try:
        a = np.array(arr, dtype=float)
        return float(np.nansum(a))
    except Exception:
        return None

def pick_hour_value(bundle: dict, hour_index: int) -> dict:
    def _get(name):
        arr = bundle.get(name, [])
        if not arr:
            return None
        idx = int(hour_index) % len(arr)
        try:
            return float(arr[idx])
        except Exception:
            return None

    return {
        "temperature": _get("temperature"),
        "humidity": _get("humidity"),
        "rainfall": _get("rainfall"),
        "wind_speed": _get("wind_speed"),
        "wind_direction": _get("wind_direction"),
    }

def aggregate_range(bundle: dict) -> dict:
    return {
        "temperature": safe_mean(bundle.get("temperature", [])),
        "humidity": safe_mean(bundle.get("humidity", [])),
        "rainfall": safe_sum(bundle.get("rainfall", [])),
        "wind_speed": safe_mean(bundle.get("wind_speed", [])),
        "wind_direction": safe_mean(bundle.get("wind_direction", [])),
    }

@app.route("/historical_point")
def historical_point():
    lat = float(request.args.get("lat"))
    lon = float(request.args.get("lon"))
    start_date = request.args.get("start", "2020-01-01")
    end_date = request.args.get("end", "2020-01-02")
    model = request.args.get("model", "era5")  # era5 أو era5_land
    mode = request.args.get("mode", "range")   # range/hour
    hour_index = int(request.args.get("h", 0))

    bundle = fetch_point_historical_bundle(lat, lon, start_date, end_date, model=model)

    if mode == "hour":
        metrics = pick_hour_value(bundle, hour_index)
    else:
        metrics = aggregate_range(bundle)

    return jsonify({
        "lat": lat,
        "lon": lon,
        "start": start_date,
        "end": end_date,
        "model": model,
        "mode": mode,
        "metrics": metrics,
        "time_len": len(bundle.get("time", []))
    })

# -------------------------
# Overlay grid (forecast OR historical)
# -------------------------
def generate_weather_data_forecast(variable: str, hour_index: int, coarse: int = 25, upscale: int = 8, smooth_sigma: float = 1.2):
    lat_min, lat_max = SAUDI_BOUNDS['lat_min'], SAUDI_BOUNDS['lat_max']
    lon_min, lon_max = SAUDI_BOUNDS['lon_min'], SAUDI_BOUNDS['lon_max']

    coarse_lats = np.linspace(lat_min, lat_max, coarse)
    coarse_lons = np.linspace(lon_min, lon_max, coarse)
    coarse_data = np.zeros((coarse, coarse), dtype=float)

    for i, lat in enumerate(coarse_lats):
        for j, lon in enumerate(coarse_lons):
            coarse_data[i, j] = fetch_point_hour(variable, lat, lon, hour_index)

    if smooth_sigma and smooth_sigma > 0:
        coarse_data = gaussian_filter(coarse_data, sigma=smooth_sigma)

    data = zoom(coarse_data, upscale, order=1)
    lats = np.linspace(lat_min, lat_max, data.shape[0])
    lons = np.linspace(lon_min, lon_max, data.shape[1])
    return lons, lats, data

def generate_weather_data_historical(variable: str, start_date: str, end_date: str, model: str, mode: str, hour_index: int,
                                    coarse: int = 25, upscale: int = 8, smooth_sigma: float = 1.2):
    lat_min, lat_max = SAUDI_BOUNDS['lat_min'], SAUDI_BOUNDS['lat_max']
    lon_min, lon_max = SAUDI_BOUNDS['lon_min'], SAUDI_BOUNDS['lon_max']

    coarse_lats = np.linspace(lat_min, lat_max, coarse)
    coarse_lons = np.linspace(lon_min, lon_max, coarse)
    coarse_data = np.zeros((coarse, coarse), dtype=float)

    for i, lat in enumerate(coarse_lats):
        for j, lon in enumerate(coarse_lons):
            bundle = fetch_point_historical_bundle(lat, lon, start_date, end_date, model=model)
            if mode == "hour":
                m = pick_hour_value(bundle, hour_index)
            else:
                m = aggregate_range(bundle)

            coarse_data[i, j] = m.get(variable, np.nan)

    if smooth_sigma and smooth_sigma > 0:
        coarse_data = gaussian_filter(coarse_data, sigma=smooth_sigma)

    data = zoom(coarse_data, upscale, order=1)
    lats = np.linspace(lat_min, lat_max, data.shape[0])
    lons = np.linspace(lon_min, lon_max, data.shape[1])
    return lons, lats, data

def get_colormap_and_range(variable):
    if variable == 'rainfall':
        cmap = plt.colormaps.get_cmap('YlGnBu')
        vmin, vmax = 0, 50
        levels = list(range(0, 11, 1)) + list(range(15, 51, 5))
        unit = 'mm'

    elif variable == 'temperature':
        cmap = plt.colormaps.get_cmap('YlOrRd')
        vmin, vmax = 10, 50
        levels = list(range(10, 51, 1))
        unit = '°C'

    elif variable == 'humidity':
        cmap = plt.colormaps.get_cmap('PuBuGn')
        vmin, vmax = 10, 100
        levels = list(range(10, 101, 5))
        unit = '%'

    elif variable == 'wind_speed':
        cmap = plt.colormaps.get_cmap('viridis')
        vmin, vmax = 0, 25
        levels = list(range(0, 26, 1))
        unit = 'm/s'

    elif variable == 'wind_direction':
        # اتجاه الرياح (0-360) صعب كـ contour “منطقي”، بس للعرض نستخدم HSV
        cmap = plt.colormaps.get_cmap('hsv')
        vmin, vmax = 0, 360
        levels = list(range(0, 361, 15))
        unit = 'deg'

    else:
        cmap = plt.colormaps.get_cmap('viridis')
        vmin, vmax = 0, 100
        levels = list(range(0, 101, 5))
        unit = ''

    return cmap, vmin, vmax, levels, unit

@app.route('/')
def index():
    timestamps = []
    base_time = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    for i in range(48):
        ts = base_time + timedelta(hours=i)
        timestamps.append(ts.strftime('%Y-%m-%dT%H:%M:%SZ'))
    return render_template('index.html', timestamps=timestamps)

@app.route('/overlay')
def overlay():
    variable = request.args.get('var', 'temperature')

    # ✅ لو جاني start/end -> historical
    start = request.args.get("start")
    end = request.args.get("end")
    model = request.args.get("model", "era5_land")
    mode = request.args.get("mode", "range")
    hour_index = int(request.args.get("h", 0))

    if start and end:
        lons, lats, data = generate_weather_data_historical(
            variable=variable,
            start_date=start,
            end_date=end,
            model=model,
            mode=mode,
            hour_index=hour_index
        )
    else:
        # forecast الحالي
        timestamp = request.args.get('t')
        if not timestamp:
            timestamp = datetime.utcnow().replace(minute=0, second=0, microsecond=0).strftime('%Y-%m-%dT%H:%M:%SZ')

        try:
            ts = datetime.strptime(timestamp, '%Y-%m-%dT%H:%M:%SZ')
            now0 = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
            hour_index = int((ts - now0).total_seconds() // 3600) % 48
        except Exception:
            hour_index = 0

        lons, lats, data = generate_weather_data_forecast(variable, hour_index)

    cmap, vmin, vmax, levels, unit = get_colormap_and_range(variable)

    fig, ax = plt.subplots(figsize=(12, 8), dpi=150)
    ax.set_xlim(SAUDI_BOUNDS['lon_min'], SAUDI_BOUNDS['lon_max'])
    ax.set_ylim(SAUDI_BOUNDS['lat_min'], SAUDI_BOUNDS['lat_max'])
    ax.axis('off')

    if levels:
        ax.contourf(lons, lats, data, levels=levels, cmap=cmap, alpha=0.8, extend='both')
    else:
        ax.contourf(lons, lats, data, levels=20, cmap=cmap, alpha=0.8, vmin=vmin, vmax=vmax)

    plt.tight_layout(pad=0)
    buf = io.BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight', pad_inches=0, transparent=True, dpi=150)
    plt.close(fig)
    buf.seek(0)
    return send_file(buf, mimetype='image/png')

@app.route('/legend')
def legend():
    variable = request.args.get('var', 'temperature')
    cmap, vmin, vmax, levels, unit = get_colormap_and_range(variable)

    fig, ax = plt.subplots(figsize=(1.1, 4.2))

    if levels and len(levels) > 1:
        norm = BoundaryNorm(levels, ncolors=cmap.N, clip=True)
        sm = cm.ScalarMappable(norm=norm, cmap=cmap)
        cb = plt.colorbar(sm, cax=ax, orientation='vertical')

        if variable == "temperature":
            ticks = list(range(levels[0], levels[-1] + 1, 5))
        elif variable == "humidity":
            ticks = list(range(levels[0], levels[-1] + 1, 10))
        elif variable == "wind_speed":
            ticks = list(range(0, 26, 5))
        elif variable == "wind_direction":
            ticks = list(range(0, 361, 90))
        else:
            ticks = [0, 1, 2, 5, 10, 20, 30, 40, 50]

        cb.set_ticks(ticks)
        cb.set_ticklabels([str(t) for t in ticks])
    else:
        norm = Normalize(vmin=vmin, vmax=vmax)
        cb = plt.colorbar(cm.ScalarMappable(norm=norm, cmap=cmap), cax=ax, orientation='vertical')

    cb.set_label(unit, fontsize=11, weight='bold')
    cb.ax.tick_params(labelsize=9)

    buf = io.BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight', transparent=True, dpi=160)
    plt.close(fig)
    buf.seek(0)
    return send_file(buf, mimetype='image/png')

@app.route('/point')
def point_value():
    lat = float(request.args.get('lat'))
    lon = float(request.args.get('lon'))
    variable = request.args.get('var', 'temperature')
    hour_index = int(request.args.get('h', 0)) % 48
    val = fetch_point_hour(variable, lat, lon, hour_index)
    return jsonify({"lat": lat, "lon": lon, "variable": variable, "hour_index": hour_index, "value": val})

# -------------------------
# Wind Rose endpoint ✅
# -------------------------
def _wind_dir_labels_16():
    return ["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSW","SW","WSW","W","WNW","NW","NNW"]

def _dir_to_sector_16(deg):
    # 0..360 -> 16 sectors (each 22.5deg)
    if deg is None or np.isnan(deg):
        return None
    deg = float(deg) % 360.0
    idx = int(np.floor((deg + 11.25) / 22.5)) % 16
    return idx

@app.route("/windrose")
def windrose():
    lat = float(request.args.get("lat"))
    lon = float(request.args.get("lon"))
    start = request.args.get("start")
    end = request.args.get("end")
    model = request.args.get("model", "era5_land")

    if not start or not end:
        return jsonify({"error": "start/end required"}), 400

    cache_key = ("wr", model, round(lat,3), round(lon,3), start, end)
    cached = WINDROSE_CACHE.get(cache_key)
    if cached is not None:
        return jsonify(cached)

    bundle = fetch_point_historical_bundle(lat, lon, start, end, model=model)
    ws = np.array(bundle.get("wind_speed", []), dtype=float)
    wd = np.array(bundle.get("wind_direction", []), dtype=float)

    if ws.size == 0 or wd.size == 0:
        out = {
            "direction_labels": _wind_dir_labels_16(),
            "speed_bins": [0, 2, 4, 6, 8, 10, 15, 20, 30],
            "matrix": [[0]*8 for _ in range(16)]
        }
        WINDROSE_CACHE.set(cache_key, out)
        return jsonify(out)

    # bins
    speed_bins = np.array([0, 2, 4, 6, 8, 10, 15, 20, 30], dtype=float)  # 8 ranges
    n_dir = 16
    n_bins = len(speed_bins) - 1

    mat = np.zeros((n_dir, n_bins), dtype=float)

    # build
    valid = ~(np.isnan(ws) | np.isnan(wd))
    ws = ws[valid]
    wd = wd[valid]

    for s, d in zip(ws, wd):
        di = _dir_to_sector_16(d)
        if di is None:
            continue
        bi = np.searchsorted(speed_bins, s, side="right") - 1
        if 0 <= bi < n_bins:
            mat[di, bi] += 1

    total = mat.sum()
    if total > 0:
        mat = (mat / total) * 100.0  # percentage

    out = {
        "direction_labels": _wind_dir_labels_16(),
        "speed_bins": speed_bins.tolist(),
        "matrix": mat.round(2).tolist()
    }
    WINDROSE_CACHE.set(cache_key, out)
    return jsonify(out)

def warm_cache_background():
    keypoints = [
        (24.7136, 46.6753), # Riyadh
        (21.3891, 39.8579), # Jeddah
        (21.4225, 39.8262), # Makkah
        (26.4207, 50.0888), # Dammam
        (18.2164, 42.5053), # Abha
    ]
    while True:
        try:
            for var in ["rainfall", "temperature", "humidity", "wind_speed", "wind_direction"]:
                for h in range(0, 48, 3):
                    for (la, lo) in keypoints:
                        _ = fetch_point_hour(var, la, lo, h)
        except Exception:
            pass
        import time as _t
        _t.sleep(600)

if __name__ == '__main__':
    Thread(target=warm_cache_background, daemon=True).start()
    debug_mode = os.environ.get('FLASK_DEBUG', 'False').lower() in ('true', '1', 't')
    app.run(host='0.0.0.0', port=5001, debug=debug_mode)
