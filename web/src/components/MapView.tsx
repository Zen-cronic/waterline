"use client";
import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

export type LayerEvent = {
  layer: string; label: string; rev: string; status: string;
  rowCount: number; geojson?: GeoJSON.FeatureCollection;
};
export type MapHandle = { setLayer: (e: LayerEvent) => void; reset: () => void };

// OpenFreeMap's public MapLibre style requires no registration or API key and
// carries the required OpenMapTiles/OpenStreetMap attribution in the style.
const STYLES = {
  dark: "https://tiles.openfreemap.org/styles/dark",
  light: "https://tiles.openfreemap.org/styles/positron",
} as const;

// Paint spec per layer id: how each streamed layer renders.
const PAINT: Record<string, (theme: "dark" | "light") => maplibregl.LayerSpecification[]> = {
  corridor: () => [{ id: "corridor", type: "fill", source: "corridor",
    paint: { "fill-color": "#35d0d6", "fill-opacity": 0.08 } },
    { id: "corridor-line", type: "line", source: "corridor",
    paint: { "line-color": "#35d0d6", "line-opacity": 0.4, "line-width": 1 } }],
  route: () => [{ id: "route", type: "line", source: "route",
    paint: { "line-color": "#f2b45a", "line-width": 2.5, "line-dasharray": [2, 1] } }],
  notams: (theme) => [{ id: "notams", type: "circle", source: "notams",
    paint: {
      "circle-radius": ["case", ["get", "memory_changed"], 8, 5],
      "circle-color": ["case", ["get", "memory_changed"], "#ff4f91",
        ["get", "fir_wide"], "#ef6b6b", theme === "light" ? "#263d4a" : "#e6eef7"],
      "circle-opacity": 0.85, "circle-stroke-color": theme === "light" ? "#ffffff" : "#0a0f16", "circle-stroke-width": 1 } }],
  stations: (theme) => [{ id: "stations", type: "circle", source: "stations",
    paint: { "circle-radius": 7, "circle-color": "#57c98a",
      "circle-stroke-color": theme === "light" ? "#ffffff" : "#0a0f16", "circle-stroke-width": 2 } },
    { id: "stations-label", type: "symbol", source: "stations",
    layout: {
      "text-field": ["get", "station_id"], "text-font": ["Noto Sans Regular"],
      "text-size": 11, "text-offset": [0, 1.4],
    },
    paint: { "text-color": "#57c98a" } }],
};

export const MapView = forwardRef<MapHandle, { theme: "dark" | "light" }>(function MapView({ theme }, ref) {
  const holder = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const ready = useRef(false);
  const pending = useRef<LayerEvent[]>([]);
  const retained = useRef(new Map<string, LayerEvent>());

  useEffect(() => {
    if (!holder.current || map.current) return;
    const m = new maplibregl.Map({
      container: holder.current, style: STYLES[theme], center: [-80.2, 45.6], zoom: 5.2,
      attributionControl: { compact: true },
    });
    // The public style can reference optional POI sprites that are absent from
    // its sprite sheet. Waterline does not use those icons, so supply a
    // transparent placeholder instead of emitting noisy browser warnings.
    m.on("styleimagemissing", (event) => {
      if (!m.hasImage(event.id)) {
        m.addImage(event.id, { width: 1, height: 1, data: new Uint8Array([0, 0, 0, 0]) });
      }
    });
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
    m.on("load", () => {
      ready.current = true;
      const layers = pending.current.length ? pending.current : [...retained.current.values()];
      layers.forEach(apply);
      pending.current = [];
    });
    map.current = m;
    return () => { m.remove(); map.current = null; ready.current = false; };
  }, [theme]);

  function apply(e: LayerEvent) {
    const m = map.current;
    if (!m || !e.geojson) return;
    const src = m.getSource(e.layer) as maplibregl.GeoJSONSource | undefined;
    if (src) { src.setData(e.geojson); return; }          // update in place (rev semantics)
    m.addSource(e.layer, { type: "geojson", data: e.geojson });
    (PAINT[e.layer]?.(theme) ?? []).forEach((spec) => { if (!m.getLayer(spec.id)) m.addLayer(spec); });
    if (e.layer === "route") {                              // fit to the flown route once it exists
      const coords = (e.geojson.features[0]?.geometry as GeoJSON.LineString)?.coordinates;
      if (coords?.length) {
        const b = coords.reduce((bb: maplibregl.LngLatBounds, c: number[]) => bb.extend(c as [number, number]),
          new maplibregl.LngLatBounds(coords[0] as [number, number], coords[0] as [number, number]));
        m.fitBounds(b, { padding: 90, duration: 900 });
      }
    }
  }

  useImperativeHandle(ref, () => ({
    setLayer: (e) => {
      retained.current.set(e.layer, e);
      ready.current ? apply(e) : pending.current.push(e);
    },
    reset: () => {
      retained.current.clear();
      const m = map.current; if (!m) return;
      ["corridor", "corridor-line", "route", "notams", "stations", "stations-label"].forEach((id) => {
        if (m.getLayer(id)) m.removeLayer(id);
      });
      ["corridor", "route", "notams", "stations"].forEach((id) => { if (m.getSource(id)) m.removeSource(id); });
    },
  }));

  return <div id="map" ref={holder} />;
});
