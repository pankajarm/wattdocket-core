import { useEffect, useRef, useState } from 'react';
import type { RefObject } from 'react';

/** Hash route: "#/public/echo:123" → { name: 'public', param: 'echo:123' } */
export type Route =
  | { name: 'search'; query: string }
  | { name: 'address'; query: string }
  | { name: 'public'; facilityId: string }
  | { name: 'operator'; facilityId: string }
  | { name: 'data' }
  | { name: 'grid' }
  | { name: 'notFound'; path: string };

function parse(hash: string): Route {
  const raw = hash.replace(/^#/, '');
  const [pathPart, queryPart] = raw.split('?');
  const path = (pathPart ?? '').replace(/^\/+/, '');
  const params = new URLSearchParams(queryPart ?? '');
  const segments = path.split('/').filter(Boolean);

  if (segments.length === 0) return { name: 'search', query: params.get('q') ?? '' };

  const [head, id] = segments;
  if (head === 'data' && segments.length === 1) return { name: 'data' };
  if (head === 'grid' && segments.length === 1) return { name: 'grid' };
  if (head === 'address') return { name: 'address', query: params.get('q') ?? '' };
  if ((head === 'public' || head === 'operator') && id) {
    try {
      return { name: head, facilityId: decodeURIComponent(id) };
    } catch {
      // A malformed shared URL should show the normal not-found page rather
      // than crash the entire application during route parsing.
      return { name: 'notFound', path: raw };
    }
  }
  return { name: 'notFound', path: raw };
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parse(window.location.hash));
  useEffect(() => {
    const onChange = () => setRoute(parse(window.location.hash));
    window.addEventListener('hashchange', onChange);
    return () => window.removeEventListener('hashchange', onChange);
  }, []);
  return route;
}

export function navigate(to: string): void {
  const next = to.startsWith('#') ? to : `#${to}`;
  if (window.location.hash === next) return;
  window.location.hash = next;
}

export function publicHref(facilityId: string): string {
  return `#/public/${encodeURIComponent(facilityId)}`;
}

export function operatorHref(facilityId: string): string {
  return `#/operator/${encodeURIComponent(facilityId)}`;
}

/**
 * Width of an element, for hand-drawn SVG that must not stretch its labels.
 * Measure an inner wrapper that carries no padding; the drawing is sized to it.
 */
export function useElementWidth<T extends HTMLElement>(): [RefObject<T>, number] {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);

  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const update = () => setWidth(node.clientWidth);
    update();
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(update);
    ro.observe(node);
    return () => ro.disconnect();
  }, []);

  return [ref, width];
}
