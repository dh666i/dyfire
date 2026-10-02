export function getMobileDockRoutes(routes, adminRoutes, admin) {
  if (admin) {
    const primary = adminRoutes.slice(0, 4);
    const operations = adminRoutes.find(([href]) => href === '/admin/operations');
    return operations && !primary.some(([href]) => href === operations[0])
      ? [...primary, operations]
      : primary;
  }
  return [routes[0], routes[4], ...routes.slice(1, 4)];
}

export function focusTrapTarget({key, shiftKey, activeElement, focusable, contains}) {
  if (key !== 'Tab' || !focusable.length) return null;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (!contains(activeElement)) return shiftKey ? last : first;
  if (shiftKey && activeElement === first) return last;
  if (!shiftKey && activeElement === last) return first;
  return null;
}

export function resetRouteScroll(location, windowObject = window) {
  if (location.hash) return;
  windowObject.scrollTo(0, 0);
}
