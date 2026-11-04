import { useEffect, useMemo, useRef, useState } from 'react';
import L from 'leaflet';
import 'leaflet.markercluster';
import 'leaflet/dist/leaflet.css';
import 'leaflet.markercluster/dist/MarkerCluster.css';
import './atlas-map.css';
import type { FacilityIndexEntry } from '../types';

interface Props {
  facilities: FacilityIndexEntry[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  /** Increment on an explicit user selection, including reselecting the same site. */
  focusToken?: number;
}

type LocatedFacility = FacilityIndexEntry & { lat: number; lon: number };
type Site = { representative: LocatedFacility; ids: string[] };

function validCoordinates(facility: FacilityIndexEntry): facility is LocatedFacility {
  return facility.location_quality?.map_eligible !== false && typeof facility.lat === 'number' && typeof facility.lon === 'number' && Number.isFinite(facility.lat) && Number.isFinite(facility.lon) &&
    facility.lat >= -90 && facility.lat <= 90 && facility.lon >= -180 && facility.lon <= 180 &&
    !(facility.lat === 0 && facility.lon === 0);
}

/** One map marker per site, even when ECHO has multiple permit records. */
function groupSites(facilities: FacilityIndexEntry[]): Site[] {
  const sites = new Map<string, Site>();
  facilities.filter(validCoordinates).forEach((facility) => {
    const key = facility.site_id || facility.registry_id || facility.id;
    const site = sites.get(key);
    if (site) site.ids.push(facility.id);
    else sites.set(key, { representative: facility, ids: [facility.id] });
  });
  return [...sites.values()];
}

function siteIcon(selected: boolean): L.DivIcon {
  return L.divIcon({
    className: 'atlas-marker-wrap',
    html: `<span class="atlas-marker${selected ? ' atlas-marker--selected' : ''}"></span>`,
    iconSize: [44, 44], iconAnchor: [22, 22],
  });
}

/** Map is a geographic index, not an assertion of facility footprints or activity. */
export function AtlasMap({ facilities, selectedId, onSelect, focusToken = 0 }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const clusterRef = useRef<L.MarkerClusterGroup | null>(null);
  const markersRef = useRef<Map<string, L.Marker>>(new Map());
  const onSelectRef = useRef(onSelect);
  const initialSitesRef = useRef(false);
  const previousSitesRef = useRef('');
  const previousSelectedRef = useRef<string | null>(null);
  const previousFocusTokenRef = useRef(focusToken);
  const fitTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [tileUnavailable, setTileUnavailable] = useState(false);
  const sites = useMemo(() => groupSites(facilities), [facilities]);
  onSelectRef.current = onSelect;

  useEffect(() => {
    if (!containerRef.current) return;
    const map = L.map(containerRef.current, {
      zoomControl: false, scrollWheelZoom: false, attributionControl: true,
    }).setView([38.5, -96], 4);
    L.control.zoom({ position: 'bottomright' }).addTo(map);
    const tileLayer = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    });
    let failures = 0;
    tileLayer.on('tileerror', () => {
      failures += 1;
      if (failures >= 3) setTileUnavailable(true);
    });
    tileLayer.on('load', () => {
      failures = 0;
      setTileUnavailable(false);
    });
    tileLayer.addTo(map);
    const clusters = L.markerClusterGroup({
      showCoverageOnHover: false,
      maxClusterRadius: 42,
      spiderfyOnMaxZoom: true,
      spiderLegPolylineOptions: { className: 'atlas-spider-leg', weight: 1.5, opacity: .85 },
      // Filters replace the marker collection while the user navigates. Do
      // not leave pending cluster animations referring to removed markers.
      animate: false,
      iconCreateFunction: (cluster) => L.divIcon({
        className: 'atlas-cluster-wrap',
        html: `<span class="atlas-cluster">${cluster.getChildCount()}</span>`,
        iconSize: [44, 44],
      }),
    }).addTo(map);
    mapRef.current = map;
    clusterRef.current = clusters;
    const observer = new ResizeObserver(() => map.invalidateSize({ pan: false }));
    observer.observe(containerRef.current);
    return () => {
      observer.disconnect();
      if (fitTimerRef.current) clearTimeout(fitTimerRef.current);
      map.remove();
      mapRef.current = null;
      clusterRef.current = null;
    };
  }, []);

  useEffect(() => {
    const clusters = clusterRef.current;
    const map = mapRef.current;
    if (!clusters || !map) return;
    clusters.clearLayers();
    const markers = new Map<string, L.Marker>();
    sites.forEach(({ representative: facility, ids }) => {
      const marker = L.marker([facility.lat, facility.lon], {
        icon: siteIcon(ids.includes(selectedId ?? '')),
        title: `${facility.name}, ${facility.city}, ${facility.state}`,
        alt: `${facility.name}, ${facility.city}, ${facility.state}`,
        keyboard: true,
      });
      marker.on('click', () => onSelectRef.current(facility.id));
      const tooltip = document.createElement('span');
      tooltip.textContent = `${facility.name} · ${facility.city}, ${facility.state}`;
      marker.bindTooltip(tooltip, { direction: 'top', offset: [0, -12] });
      ids.forEach((id) => markers.set(id, marker));
      clusters.addLayer(marker);
    });
    markersRef.current = markers;
    const signature = sites.map(({ representative }) => representative.id).join('|');
    if (fitTimerRef.current) clearTimeout(fitTimerRef.current);
    if (!initialSitesRef.current && sites.length) {
      // The initial hero is a preview, not a request to zoom into that site.
      initialSitesRef.current = true;
      previousSelectedRef.current = selectedId;
      previousFocusTokenRef.current = focusToken;
    } else if (initialSitesRef.current && signature !== previousSitesRef.current && sites.length) {
      // Debounce text search so the map does not jump after each keystroke.
      const positions = sites.map(({ representative: site }) => [site.lat, site.lon] as L.LatLngTuple);
      fitTimerRef.current = setTimeout(() => {
        map.fitBounds(L.latLngBounds(positions), { padding: [35, 35], maxZoom: 9, animate: false });
        fitTimerRef.current = null;
      }, 600);
    }
    previousSitesRef.current = signature;
  }, [sites]);

  useEffect(() => {
    const map = mapRef.current;
    const clusters = clusterRef.current;
    if (!map || !clusters) return;
    const seen = new Set<L.Marker>();
    markersRef.current.forEach((marker) => {
      if (seen.has(marker)) return;
      seen.add(marker);
      marker.setIcon(siteIcon(selectedId != null && markersRef.current.get(selectedId) === marker));
    });
    const selected = selectedId ? markersRef.current.get(selectedId) : undefined;
    const explicitSelection = selectedId !== previousSelectedRef.current || focusToken !== previousFocusTokenRef.current;
    previousSelectedRef.current = selectedId;
    previousFocusTokenRef.current = focusToken;
    if (selected && explicitSelection) {
      if (fitTimerRef.current) { clearTimeout(fitTimerRef.current); fitTimerRef.current = null; }
      // zoomToShowLayer registers deferred cluster listeners. Clearing and
      // rebuilding layers during a search can detach that marker before the
      // listener runs, causing a Leaflet error and a blank React page. Center
      // immediately instead; the list and detail remain available even when
      // nearby markers are still grouped at this zoom level.
      map.setView(selected.getLatLng(), Math.max(map.getZoom(), 14), { animate: false });
    }
  }, [selectedId, sites, focusToken]);

  return (
    <div className="atlas-map-shell">
      <div ref={containerRef} className="atlas-map" aria-label="Map of listed facilities. Use the results list to select a record without the map." />
      {tileUnavailable && <div className="atlas-map-notice" role="status">Map background unavailable. Site markers and the results list remain available.</div>}
      <div className="atlas-map-legend"><span className="atlas-map-legend__dot" aria-hidden="true" /><span>Listed facility</span><span className="atlas-map-legend__divider" aria-hidden="true">·</span><span className="atlas-map-legend__explanation">Clusters group nearby sites</span></div>
    </div>
  );
}
