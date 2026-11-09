/**
 * Baked into the page. `/api/health` returns the same number from
 * `backend/app/__init__.py`. A tab left open across a deploy is outdated when
 * the server version is newer. Keep this, `frontend/package.json` `version`,
 * and backend `__version__` the same.
 */
export const APP_VERSION = '0.1.0';

function parseSemver(value: string): [number, number, number] | null {
  const m = /^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?/.exec(value);
  if (!m) return null;
  return [Number(m[1]), Number(m[2] ?? 0), Number(m[3] ?? 0)];
}

/** True when this page should be reloaded to match the server it is talking to. */
export function isFrontendOutdated(frontend: string, backend: string | null | undefined): boolean {
  const page = frontend.trim();
  const server = (backend ?? '').trim();
  if (!page || !server) return false;
  const a = parseSemver(page);
  const b = parseSemver(server);
  if (a && b) {
    for (let i = 0; i < 3; i++) {
      if (a[i] < b[i]) return true;
      if (a[i] > b[i]) return false;
    }
    return false;
  }
  return page !== server;
}
