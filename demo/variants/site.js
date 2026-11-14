// Shared by the public pages: the satellite + 3D-buildings map, and the camera that flies to a building and circles it.
// Needs maplibre-gl loaded first. A building is {lat, lon, stories}.
const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;

const inRing = ([x, y], ring) => { let c = false; for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) { const [xi, yi] = ring[i], [xj, yj] = ring[j]; if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) c = !c; } return c; };
console.assert(inRing([1, 1], [[0, 0], [2, 0], [2, 2], [0, 2]]) && !inRing([3, 1], [[0, 0], [2, 0], [2, 2], [0, 2]]));

function makeMap(container, opts = {}) {
  const map = new maplibregl.Map({
    container, center: [-106.9, 32.2], zoom: 3.6, maxPitch: 70, attributionControl: { compact: true }, ...opts,
    style: { version: 8, projection: { type: "globe" },
      sky: { "sky-color": "#0a1320", "horizon-color": "#16263a", "fog-color": "#0a1320", "atmosphere-blend": ["interpolate", ["linear"], ["zoom"], 0, 1, 5, 1, 7, 0] },
      sources: {
        sat: { type: "raster", tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"], tileSize: 256, maxzoom: 19, attribution: "Imagery &copy; Esri, Maxar, Earthstar Geographics" },
        omt: { type: "vector", url: "https://tiles.openfreemap.org/planet" } },
      layers: [
        { id: "bg", type: "background", paint: { "background-color": "#05080c" } },
        { id: "sat", type: "raster", source: "sat", paint: { "raster-saturation": -0.6, "raster-brightness-max": 0.62, "raster-contrast": 0.15 } },
        { id: "bld", type: "fill-extrusion", source: "omt", "source-layer": "building", minzoom: 14.5,
          paint: { "fill-extrusion-color": "#3b4a5c", "fill-extrusion-height": ["coalesce", ["get", "render_height"], 6], "fill-extrusion-base": ["coalesce", ["get", "render_min_height"], 0], "fill-extrusion-opacity": 0.9 } }] } });
  map.on("load", () => {
    map.addSource("sel", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    map.addLayer({ id: "sel", type: "fill-extrusion", source: "sel", paint: { "fill-extrusion-color": "#ff5a36", "fill-extrusion-height": ["+", ["coalesce", ["get", "render_height"], 6], 0.5], "fill-extrusion-base": ["coalesce", ["get", "render_min_height"], 0], "fill-extrusion-opacity": 0.8 } });
  });
  const target = document.createElement("div"); target.className = "target";
  const marker = new maplibregl.Marker({ element: target });
  // The chosen building's own footprint, raised in the accent colour: the footprint that contains the building's point.
  function highlight(b) {
    const src = map.getSource("sel");
    if (!src) return false;
    const hit = b ? map.querySourceFeatures("omt", { sourceLayer: "building" }).filter(f => (f.geometry.type === "Polygon" ? [f.geometry.coordinates] : f.geometry.type === "MultiPolygon" ? f.geometry.coordinates : []).some(poly => inRing([b.lon, b.lat], poly[0]))) : [];
    src.setData({ type: "FeatureCollection", features: hit.map(f => ({ type: "Feature", properties: f.properties, geometry: f.geometry })) });
    if (hit.length) marker.remove();  // the raised footprint marks it better than a ring that nearer towers can sit behind
    return hit.length > 0;
  }
  let orbit = 0;  // bumped whenever the camera gets a new job, so an old orbit stops itself
  function spin(id) {
    if (id !== orbit) return;
    map.easeTo({ bearing: map.getBearing() + 90, duration: 24000, easing: t => t });
    map.once("moveend", () => spin(id));
  }
  function fly(b) {
    if (!b.lat) return;
    const id = ++orbit, to = { center: [b.lon, b.lat], zoom: 17.2 - Math.min(1.4, (b.stories || 0) / 50), pitch: 62 };  // pull back for towers
    marker.setLngLat(to.center).addTo(map); highlight(null);
    const arrive = () => { if (id === orbit && !highlight(b)) setTimeout(() => id === orbit && highlight(b), 1500); };  // tiles may still be loading
    if (REDUCED) { map.jumpTo(to); return map.once("idle", arrive); }
    map.flyTo({ ...to, speed: 1.1, curve: 1.6, essential: true });
    map.once("moveend", () => { arrive(); spin(id); });
  }
  const hold = () => { orbit++; map.stop(); };              // stop circling (the map is off screen, or the user took the camera)
  const resume = () => { if (!REDUCED) spin(++orbit); };
  for (const ev of ["mousedown", "touchstart", "wheel"]) map.on(ev, hold);
  return { map, fly, hold, resume };
}
