// Minimal hash-based router for the v2 cloud auth routes.
//
// The backend's verification / reset emails link to
// ``{base}/#/verify-email?token=...`` (see backend/app/emailing.py), so
// the frontend must route on the URL *hash* — a path-based router would
// never see those links. Zero new dependencies: parsing is a plain
// string split and navigation is a plain hash assignment, which the
// browser turns into a hashchange event.

import { useEffect, useState } from 'react';

export type Route = {
  path: string;
  query: URLSearchParams;
};

export function parseHash(hash: string): Route {
  const raw = hash.replace(/^#/, '');
  const questionIndex = raw.indexOf('?');
  const rawPath = questionIndex >= 0 ? raw.slice(0, questionIndex) : raw;
  const rawQuery = questionIndex >= 0 ? raw.slice(questionIndex + 1) : '';
  let path = rawPath || '/';
  if (!path.startsWith('/')) {
    path = `/${path}`;
  }
  return { path, query: new URLSearchParams(rawQuery) };
}

export function navigate(to: string): void {
  const target = to.startsWith('#') ? to : `#${to.startsWith('/') ? to : `/${to}`}`;
  if (window.location.hash === target) {
    return;
  }
  window.location.hash = target;
}

export function useHashRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash));

  useEffect(() => {
    const handleHashChange = () => setRoute(parseHash(window.location.hash));
    window.addEventListener('hashchange', handleHashChange);
    return () => window.removeEventListener('hashchange', handleHashChange);
  }, []);

  return route;
}

export function routeToString(route: Route): string {
  const query = route.query.toString();
  return query ? `${route.path}?${query}` : route.path;
}

export function isAuthRoute(path: string): boolean {
  return (
    path === '/login' ||
    path === '/register' ||
    path === '/check-email' ||
    path === '/forgot-password' ||
    path === '/reset-password' ||
    path === '/verify-email'
  );
}
