// Shared by the public pages: the satellite + 3D-buildings map, and the camera that flies to a building and circles it.
// Needs maplibre-gl loaded first. A building is {lat, lon, stories}.
const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;

const inRing = ([x, y], ring) => { let c = false; for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) { const [xi, yi] = ring[i], [xj, yj] = ring[j]; if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) c = !c; } return c; };
console.assert(inRing([1, 1], [[0, 0], [2, 0], [2, 2], [0, 2]]) && !inRing([3, 1], [[0, 0], [2, 0], [2, 2], [0, 2]]));

function makeMap(container, opts = {}) {
  const map = new maplibregl.Map({
    // No attribution button on the map: each page credits the imagery and map data in its own text instead.
    container, center: [-106.9, 32.2], zoom: 3.6, maxPitch: 70, attributionControl: false, ...opts,
    style: { version: 8, projection: { type: "globe" },
      sky: { "sky-color": "#0a1320", "horizon-color": "#16263a", "fog-color": "#0a1320", "atmosphere-blend": ["interpolate", ["linear"], ["zoom"], 0, 1, 5, 1, 7, 0] },
      sources: {
        sat: { type: "raster", tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"], tileSize: 256, maxzoom: 19, attribution: "Imagery &copy; Esri, Maxar, Earthstar Geographics" },
        omt: { type: "vector", url: "https://tiles.openfreemap.org/planet" } },
      layers: [
        { id: "bg", type: "background", paint: { "background-color": "#05080c" } },
        { id: "sat", type: "raster", source: "sat", paint: { "raster-saturation": -0.6, "raster-brightness-max": 0.62, "raster-contrast": 0.15 } },
        { id: "bld", type: "fill-extrusion", source: "omt", "source-layer": "building", minzoom: 14.5,
          // The chosen building is recoloured in place through feature state. Drawing a second extrusion over it flickers:
          // two shapes on the same walls fight for the same pixels as the camera moves.
          paint: { "fill-extrusion-color": ["case", ["boolean", ["feature-state", "sel"], false], "#ff5a36", "#3b4a5c"], "fill-extrusion-height": ["coalesce", ["get", "render_height"], 6], "fill-extrusion-base": ["coalesce", ["get", "render_min_height"], 0], "fill-extrusion-opacity": 0.9 } }] } });
  const target = document.createElement("div"); target.className = "target";
  const marker = new maplibregl.Marker({ element: target });
  // Buildings with no height in OpenStreetMap arrive merged into one big feature per tile, and colouring that by feature
  // state lights up the whole neighbourhood. For those, only the footprint that holds the point is drawn, in this overlay
  // layer, grown a few percent and a little taller so its walls never sit exactly on the grey ones.
  map.on("load", () => {
    map.addSource("sel", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    map.addLayer({ id: "sel", type: "fill-extrusion", source: "sel", paint: { "fill-extrusion-color": "#ff5a36", "fill-extrusion-height": ["+", ["coalesce", ["get", "render_height"], 6], 1.5], "fill-extrusion-base": ["coalesce", ["get", "render_min_height"], 0], "fill-extrusion-opacity": 0.9 } });
  });
  const grow = (ring, k = 1.05) => { const cx = ring.reduce((t, p) => t + p[0], 0) / ring.length, cy = ring.reduce((t, p) => t + p[1], 0) / ring.length; return ring.map(([x, y]) => [cx + (x - cx) * k, cy + (y - cy) * k]); };
  let lone = [];  // the overlay footprints on show
  // Colour the chosen building: every footprint that contains the building's point (a tower can be several parts and tile pieces).
  // keep = true adds this building to the ones already coloured instead of replacing them.
  function highlight(b, keep) {
    if (!map.getSource("omt") || !map.getSource("sel")) return false;
    const where = { source: "omt", sourceLayer: "building" };
    if (!keep) { map.removeFeatureState(where); lone = []; }
    let found = 0;
    for (const f of b ? map.querySourceFeatures("omt", { sourceLayer: "building" }) : []) {
      const polys = f.geometry.type === "Polygon" ? [f.geometry.coordinates] : f.geometry.type === "MultiPolygon" ? f.geometry.coordinates : [];
      const own = polys.filter(poly => inRing([b.lon, b.lat], poly[0]));
      if (!own.length) continue;
      found++;
      if (f.id != null && polys.length <= 6) map.setFeatureState({ ...where, id: f.id }, { sel: true });  // one real building
      else lone.push(...own.map(poly => ({ type: "Feature", properties: f.properties, geometry: { type: "Polygon", coordinates: [grow(poly[0])] } })));
    }
    map.getSource("sel").setData({ type: "FeatureCollection", features: lone });
    if (found) marker.remove();  // the coloured building marks it better than a ring that nearer towers can sit behind
    return found > 0;
  }
  let orbit = 0;  // bumped whenever the camera gets a new job, so an old orbit stops itself
  function spin(id) {
    if (id !== orbit) return;
    map.easeTo({ bearing: map.getBearing() + 90, duration: 24000, easing: t => t });
    map.once("moveend", () => spin(id));
  }
  // view overrides the camera, e.g. {zoom: 15, pitch: 0} for a wider or a top-down look.
  function fly(b, view) {
    if (!b.lat) return;
    const id = ++orbit, to = { center: [b.lon, b.lat], zoom: 17.2 - Math.min(1.4, (b.stories || 0) / 50), pitch: 62, ...view };  // pull back for towers
    marker.setLngLat(to.center).addTo(map); highlight(null);
    const arrive = () => { if (id === orbit && !highlight(b)) setTimeout(() => id === orbit && highlight(b), 1500); };  // tiles may still be loading
    if (REDUCED) { map.jumpTo(to); return map.once("idle", arrive); }
    map.flyTo({ ...to, speed: 1.1, curve: 1.6, essential: true });
    map.once("moveend", () => { arrive(); spin(id); });
  }
  const hold = () => { orbit++; map.stop(); };              // stop circling (the map is off screen, or the user took the camera)
  const resume = () => { if (!REDUCED) spin(++orbit); };
  for (const ev of ["mousedown", "touchstart", "wheel"]) map.on(ev, hold);
  return { map, fly, hold, resume, highlight };
}
