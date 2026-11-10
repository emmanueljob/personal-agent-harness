import {useEffect, useRef, useState} from 'react';

export type ReviewScope = 'review'|'all'|'completed'|'later'|'dismissed';
const reviewScopes: ReviewScope[] = ['review','all','completed','later','dismissed'];

export type AdminRoute = {section: string; view?: string; record?: string; card?: string; selectedCard?: string; returnRecord?: string; returnView?: string; reviewScope?: ReviewScope; reviewOffset?: number};
export function readAdminRoute(): AdminRoute {
  const params = new URLSearchParams(location.hash.slice(1));
  const scope = params.get('reviewScope') as ReviewScope | null;
  const offset = Number(params.get('reviewOffset') || 0);
  return {reviewScope: scope && reviewScopes.includes(scope) ? scope : undefined,
    reviewOffset: Number.isSafeInteger(offset) && offset>=0 && offset%40===0 ? offset : 0,
    section: params.get('section') || 'command-center', view: params.get('view') || undefined,
    record: params.get('record') || undefined, card: params.get('card') || undefined, selectedCard: params.get('selectedCard') || undefined,
    returnRecord: params.get('returnRecord') || undefined, returnView: params.get('returnView') || undefined};
}
function routeUrl(route: AdminRoute) {
  const params = new URLSearchParams();
  Object.entries(route).forEach(([key, value]) => {if (value) params.set(key, String(value))});
  return location.pathname + location.search + '#' + params;
}
export function useAdminRoute(leave: () => boolean) {
  const [route, setRoute] = useState(readAdminRoute);
  const position = useRef(history.state?.adminPosition ?? 0), restoring = useRef(false), guard = useRef(leave);
  guard.current = leave;
  useEffect(() => {
    history.replaceState({adminPosition: position.current}, '', routeUrl(readAdminRoute()));
    const pop = (event: PopStateEvent) => {
      if (restoring.current) {restoring.current = false; return}
      const target = event.state?.adminPosition ?? 0;
      if (!guard.current()) {
        restoring.current = true;
        history.go(position.current - target);
        return;
      }
      position.current = target;
      setRoute(readAdminRoute());
    };
    window.addEventListener('popstate', pop);
    return () => window.removeEventListener('popstate', pop);
  }, []);
  const push = (destination: AdminRoute) => {
    const previousView = route.record ? route.view || 'Overview' : route.returnView;
    const next = {...destination,
      reviewScope: destination.reviewScope ?? route.reviewScope,
      reviewOffset: destination.reviewOffset ?? route.reviewOffset,
      returnRecord: destination.record || destination.returnRecord || route.record || route.returnRecord,
      returnView: destination.record ? destination.view || 'Overview' : destination.returnView || previousView};
    if (routeUrl(next) === location.pathname + location.search + location.hash) return;
    position.current += 1;
    history.pushState({adminPosition: position.current}, '', routeUrl(next));
    setRoute(next);
  };
  return [route, push] as const;
}
