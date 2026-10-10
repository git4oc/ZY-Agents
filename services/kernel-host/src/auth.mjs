/**
 * auth.mjs — bearer token authentication for the host HTTP API.
 *
 * Every request to the host (other than /health) must carry
 * `Authorization: Bearer <KERNEL_HOST_TOKEN>`. The token is shared between the
 * main service and the host (see docs/03 §5). Preference order for obtaining
 * the token:
 *   1. `--auth-token-file <path>`  (read-once, then deleted — recommended)
 *   2. `KERNEL_HOST_TOKEN` env var  (fallback)
 *
 * File-based is preferred over env var: env vars leak via the process environ
 * (visible in /proc, in container inspect, and in crash dumps).
 */
import { timingSafeEqual } from 'node:crypto';
import { existsSync, readFileSync, unlinkSync } from 'node:fs';

/** Small helper to compare bytes in constant time. */
function safeEqualString(a, b) {
  const ab = Buffer.from(String(a), 'utf8');
  const bb = Buffer.from(String(b), 'utf8');
  if (ab.length !== bb.length) {
    return false;
  }
  return timingSafeEqual(ab, bb);
}

/**
 * Loads the host token from a token file or environment variable.
 * @param {{ authTokenFile?: string | null, env?: Record<string,string|undefined> }} opts
 * @returns {{ token: string, from: 'file'|'env'|'none' }}
 */
export function loadHostToken(opts = {}) {
  const env = opts.env ?? process.env;
  if (opts.authTokenFile) {
    if (existsSync(opts.authTokenFile)) {
      const token = readFileSync(opts.authTokenFile, 'utf8').trim();
      if (token) {
        // read-once: remove the file so the secret does not linger on disk
        try {
          unlinkSync(opts.authTokenFile);
        } catch {
          /* best-effort; refusing to boot is worse than a leftover file. */
        }
        return { token, from: 'file' };
      }
    }
  }
  const envToken = env.KERNEL_HOST_TOKEN;
  if (envToken) {
    return { token: envToken, from: 'env' };
  }
  return { token: '', from: 'none' };
}

/**
 * Header extraction helper for the HTTP layer.
 * @param {{ get?: (name: string) => string | undefined, headers?: Record<string,string> }} req
 */
export function extractBearerToken(req) {
  const header = (req && typeof req.get === 'function')
    ? req.get('authorization')
    : (req && req.headers && (req.headers.authorization ?? req.headers.Authorization));
  if (!header || typeof header !== 'string') {
    return null;
  }
  const m = /^Bearer\s+(.+)$/i.exec(header.trim());
  return m ? m[1] : null;
}

/**
 * Verifies a bearer token against the expected token.
 * @param {unknown} token token from the Authorization header
 * @param {string} expected the configured KERNEL_HOST_TOKEN
 * @returns {boolean}
 */
export function verifyBearer(token, expected) {
  if (!expected || typeof token !== 'string' || !token) {
    return false;
  }
  return safeEqualString(token, expected);
}
