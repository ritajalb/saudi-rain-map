// static/js/geo_layers.js

async function fetchGeo(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`Failed to fetch ${url}`);
    return await res.json();
  }
  
  function degToCardinal(deg) {
    if (deg === null || deg === undefined || isNaN(deg)) return "-";
    const dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
    const idx = Math.round(((deg % 360) / 45)) % 8;
    return dirs[idx];
  }
  
  function fmt(val, digits = 1) {
    if (val === null || val === undefined || isNaN(val)) return "-";
    return Number(val).toFixed(digits);
  }
  
  function getHourIndex() {
    const el = document.getElementById("time-slider");
    return el ? parseInt(el.value || "0", 10) : 0;
  }
  
  function getCurrentVariable() {
    const el = document.getElementById("variable-select");
    return el ? el.value : "temperature";
  }
  
  function getFeatureCentroidLatLng(layer) {
    const b = layer.getBounds();
    const c = b.getCenter();
    return { lat: c.lat, lon: c.lng };
  }
  
  function hasDateRangeSelected() {
    return Boolean(window.__DATE_START__ && (window.__DATE_END__ || window.__DATE_START__));
  }
  function getDateStart() { return window.__DATE_START__ || null; }
  function getDateEnd() { return window.__DATE_END__ || window.__DATE_START__ || null; }
  
  // ===== Map popup blocking =====
  function blockNextMapClick() {
    window.__BLOCK_MAP_CLICK__ = true;
    setTimeout(() => { window.__BLOCK_MAP_CLICK__ = false; }, 0);
  }
  
  // Forecast point
  async function fetchPointForecast(varName, lat, lon, hourIndex) {
    const url = `/point?lat=${lat}&lon=${lon}&var=${varName}&h=${hourIndex}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`Failed /point for ${varName}`);
    return await res.json();
  }
  
  // Historical metrics
  async function fetchHistoricalMetrics(lat, lon, startDate, endDate, mode = "range", hourIndex = 0) {
    const url =
      `/historical_point?lat=${lat}&lon=${lon}` +
      `&start=${encodeURIComponent(startDate)}` +
      `&end=${encodeURIComponent(endDate)}` +
      `&mode=${encodeURIComponent(mode)}` +
      `&h=${encodeURIComponent(hourIndex)}` +
      `&model=era5_land`;
  
    const res = await fetch(url);
    if (!res.ok) throw new Error("Failed /historical_point");
    return await res.json();
  }
  
  // ===== Popup HTML (includes WindRose area) =====
  function popupHtmlWithMetrics(props, metrics, selectedVar, sourceLine, popupId) {
    const name = props?.name_ar || props?.name_en || "بدون اسم";
    const type = props?.type || "";
  
    const region = props?.region_ar || props?.region_en;
    const regionLine = region
      ? `<div style="font-size:13px;opacity:.85;margin-bottom:6px"><b>المنطقة:</b> ${region}</div>`
      : "";
  
    const keys = ["temperature", "humidity", "rainfall", "wind_speed", "wind_direction"];
    const order = [selectedVar, ...keys.filter(k => k !== selectedVar)];
  
    const labels = {
      temperature: "Temperature (°C)",
      humidity: "Humidity (%)",
      rainfall: "Rainfall (mm)",
      wind_speed: "Wind Speed (m/s)",
      wind_direction: "Wind Direction (°)"
    };
  
    const values = {
      temperature: `${fmt(metrics.temperature)} °C`,
      humidity: `${fmt(metrics.humidity)} %`,
      rainfall: `${fmt(metrics.rainfall)} mm`,
      wind_speed: `${fmt(metrics.wind_speed)} m/s`,
      wind_direction: `${fmt(metrics.wind_direction, 0)}° (${degToCardinal(metrics.wind_direction)})`
    };
  
    const rows = order
      .filter(k => k in values)
      .map(k => {
        const isTop = (k === selectedVar);
        return `
          <div style="display:flex;justify-content:space-between;gap:12px;padding:4px 0;${isTop ? "font-weight:700" : ""}">
            <span style="opacity:.85">${labels[k] || k}</span>
            <span>${values[k]}</span>
          </div>
        `;
      })
      .join("");
  
    const capAr = props?.capital_ar;
    const capEn = props?.capital_en;
    const cap = (capAr || capEn)
      ? `<div style="margin-top:6px;font-size:13px;opacity:.85"><b>العاصمة:</b> ${capAr || ""} ${capEn ? `(${capEn})` : ""}</div>`
      : "";
  
    // ✅ مكان الويندروز داخل البوب أب + زر تشغيله
    return `
      <div style="width:340px">
        <div style="font-weight:800;margin-bottom:6px">${name}</div>
        <div style="font-size:13px;opacity:.85;margin-bottom:6px"><b>النوع:</b> ${type}</div>
        ${regionLine}
        ${cap}
  
        <div style="margin-top:8px;border-top:1px solid rgba(0,0,0,.08);padding-top:8px">
          ${rows}
        </div>
  
        <button class="wr-btn" data-wrbtn="${popupId}">Wind Rose</button>
        <div class="wr-wrap">
          <div id="wr-${popupId}" style="width:320px;height:320px;"></div>
          <div class="wr-note">* يظهر فقط إذا محددة Date range (Historical).</div>
        </div>
  
        <div style="margin-top:8px;font-size:12px;opacity:.65">
          *المتغير المختار يظهر أولاً.<br/>
          ${sourceLine || ""}
        </div>
      </div>
    `;
  }
  
  // ===== styles =====
  function styleGovernorates() {
    return { weight: 1, fillOpacity: 0.10 };
  }
  function styleProvinces() {
    return { weight: 2, fillOpacity: 0.05 };
  }
  function highlightStyle(baseStyle) {
    return {
      ...baseStyle,
      weight: Math.max(2, (baseStyle.weight || 1) + 1),
      fillOpacity: Math.min(0.25, (baseStyle.fillOpacity || 0.1) + 0.10)
    };
  }
  
  // ===== Build metrics =====
  async function buildMetrics(lat, lon, hourIndex) {
    const selectedVar = getCurrentVariable();
  
    if (hasDateRangeSelected()) {
      const start = getDateStart();
      const end = getDateEnd();
      const js = await fetchHistoricalMetrics(lat, lon, start, end, "range", hourIndex);
      const m = js.metrics || {};
  
      return {
        selectedVar,
        metrics: {
          temperature: m.temperature,
          humidity: m.humidity,
          rainfall: m.rainfall,
          wind_speed: m.wind_speed,
          wind_direction: m.wind_direction
        },
        sourceLine: `Historical (ERA5-Land): <b>${start}</b> → <b>${end}</b> (mean + rain sum)`
      };
    }
  
    const vars = ["temperature", "humidity", "rainfall", "wind_speed", "wind_direction"];
    const res = await Promise.all(vars.map(v => fetchPointForecast(v, lat, lon, hourIndex)));
  
    const metrics = {
      temperature: res[0]?.value,
      humidity: res[1]?.value,
      rainfall: res[2]?.value,
      wind_speed: res[3]?.value,
      wind_direction: res[4]?.value
    };
  
    return {
      selectedVar,
      metrics,
      sourceLine: `Forecast: hour index <b>${hourIndex}</b> (48h)`
    };
  }
  
  // ===== main =====
  // ✅ لاحظ: صارت تستقبل drawWindRoseInto من index.html
  async function addGeoLayers(map, drawWindRoseInto) {
    const provinces = await fetchGeo("/api/geo/provinces");
    const governorates = await fetchGeo("/api/geo/governorates");
  
    function bindFeature(layer, feature, baseStyle) {
      layer.on("click", async (e) => {
        blockNextMapClick();
        if (e?.originalEvent) {
          L.DomEvent.stopPropagation(e.originalEvent);
          L.DomEvent.preventDefault(e.originalEvent);
        }
  
        const hourIndex = getHourIndex();
        const { lat, lon } = getFeatureCentroidLatLng(layer);
  
        try {
          const { selectedVar, metrics, sourceLine } = await buildMetrics(lat, lon, hourIndex);
  
          const popupId = `${Date.now()}-${Math.floor(Math.random()*1e6)}`;
          const html = popupHtmlWithMetrics(feature.properties, metrics, selectedVar, sourceLine, popupId);
  
          layer.bindPopup(html, { maxWidth: 420 }).openPopup();
  
          // ✅ بعد ما يفتح البوب أب: نربط زر الويندروز
          setTimeout(() => {
            const btn = document.querySelector(`[data-wrbtn="${popupId}"]`);
            if (!btn) return;
  
            btn.addEventListener("click", () => {
              // ما نرسم إلا إذا فيه تاريخ
              if (!hasDateRangeSelected()) {
                const el = document.getElementById(`wr-${popupId}`);
                if (el) el.innerHTML = "<div class='wr-note'>اختاري Date range أول.</div>";
                return;
              }
              drawWindRoseInto(`wr-${popupId}`, lat, lon);
            }, { once: true });
          }, 50);
  
        } catch (err) {
          console.error(err);
          layer.bindPopup("تعذر جلب البيانات الآن.", { maxWidth: 260 }).openPopup();
        }
      });
  
      layer.on("mouseover", () => layer.setStyle(highlightStyle(baseStyle)));
      layer.on("mouseout", () => layer.setStyle(baseStyle));
    }
  
    const provincesLayer = L.geoJSON(provinces, {
      style: styleProvinces,
      onEachFeature: (feature, layer) => bindFeature(layer, feature, styleProvinces())
    }).addTo(map);
  
    const governoratesLayer = L.geoJSON(governorates, {
      style: styleGovernorates,
      onEachFeature: (feature, layer) => bindFeature(layer, feature, styleGovernorates())
    }).addTo(map);
  
    L.control.layers(
      null,
      { "المناطق": provincesLayer, "المحافظات": governoratesLayer },
      { collapsed: false }
    ).addTo(map);
  
    return { provincesLayer, governoratesLayer };
  }
  