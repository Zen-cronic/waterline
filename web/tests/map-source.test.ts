import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const mapView = readFileSync(new URL("../src/components/MapView.tsx", import.meta.url), "utf8");

test("map uses the keyless OpenFreeMap style and never requests CARTO tiles", () => {
  assert.match(mapView, /https:\/\/tiles\.openfreemap\.org\/styles\/dark/);
  assert.match(mapView, /https:\/\/tiles\.openfreemap\.org\/styles\/positron/);
  assert.match(mapView, /retained\.current\.values/);
  assert.doesNotMatch(mapView, /carto(?:cdn)?\.com/i);
  assert.doesNotMatch(mapView, /CARTO_API_KEY/);
  assert.match(mapView, /styleimagemissing/);
  assert.match(mapView, /new Uint8Array\(\[0, 0, 0, 0\]\)/);
  assert.match(mapView, /new ResizeObserver\(\(\) => m\.resize\(\)\)/);
});
