/**
 * host.mjs — kernel host process entry point.
 *
 * Parses CLI args → loads the host token → starts the HTTP server → prints the
 * readiness line → handles SIGTERM/SIGINT for graceful shutdown.
 *
 * Usage:
 *   node src/host.mjs [--port 8101] [--host 127.0.0.1] [--auth-token-file PATH] [--sessions-dir DIR]
 *
 * Token resolution order (docs/03 §5): --auth-token-file (read-once) then
 * KERNEL_HOST_TOKEN env var.
 */
import { RuntimeManager } from './runtime-manager.mjs';
import { createHostServer, startHostServer } from './http-server.mjs';
import { EventJournal } from './event-journal.mjs';
import { loadHostToken } from './auth.mjs';
import { DEFAULT_HOST, DEFAULT_PORT, KERNEL_VERSION, DSH_HOST_PROTOCOL_VERSION } from './protocol.mjs';

/** Very small argv parser. @returns {Record<string,string|boolean>} */
function parseArgs(argv) {
  const out = { _: [] };
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    if (a.startsWith('--')) {
      const eq = a.indexOf('=');
      if (eq >= 0) {
        const key = a.slice(2, eq);
        out[key] = a.slice(eq + 1);
      } else {
        const key = a.slice(2);
        const next = argv[i + 1];
        if (next !== undefined && !next.startsWith('--')) {
          out[key] = next;
          i += 1;
        } else {
          out[key] = true;
        }
      }
    } else {
      out._.push(a);
    }
  }
  return out;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const host = String(args.host ?? process.env.HOST ?? DEFAULT_HOST);
  const port = Number(args.port ?? process.env.PORT ?? DEFAULT_PORT);
  const sessionsDir = String(args['sessions-dir'] ?? process.env.KERNEL_SESSIONS_DIR ?? './data/sessions');
  const authTokenFile = args['auth-token-file'] ? String(args['auth-token-file']) : null;

  // ---- host token ----
  const { token, from } = loadHostToken({ authTokenFile });
  if (!token) {
    console.error(
      '[kernel-host] missing KERNEL_HOST_TOKEN (or --auth-token-file). ' +
      'Refusing to boot without a shared host token. See docs/03 §5.',
    );
    process.exit(1);
  }
  console.log(`[kernel-host] host token source: ${from}`);

  // ---- runtime ledger + host journal ----
  const runtimes = new RuntimeManager({ sessionsDir });
  const journal = new EventJournal({ filePath: `${sessionsDir}/host-events.jsonl` });

  // ---- HTTP server ----
  const server = createHostServer({ runtimes, token, journal, logger: console });
  const info = await startHostServer(server, { host, port });

  // Readiness handshake line (docs/03 §5) — consumed by the main service.
  console.log(info.ready);

  // ---- graceful shutdown ----
  let shuttingDown = false;
  const shutdown = (signal) => {
    if (shuttingDown) {
      return;
    }
    shuttingDown = true;
    console.log(`[kernel-host] received ${signal}; draining runtimes...`);
    server.close(async () => {
      for (const id of runtimes.list().map((r) => r.runtime_id)) {
        await runtimes.dispose(id).catch(() => {});
      }
      journal.close();
      console.log(`[kernel-host] shutdown complete (kernel=${KERNEL_VERSION}, protocol=${DSH_HOST_PROTOCOL_VERSION})`);
      process.exit(0);
    });
    // hard-kill fallback
    setTimeout(() => process.exit(0), 10000).unref();
  };
  process.on('SIGTERM', () => shutdown('SIGTERM'));
  process.on('SIGINT', () => shutdown('SIGINT'));
  process.on('uncaughtException', (err) => {
    console.error('[kernel-host] uncaughtException', err);
    shutdown('uncaughtException');
  });
  process.on('unhandledRejection', (reason) => {
    console.error('[kernel-host] unhandledRejection', reason);
  });
}

main().catch((err) => {
  console.error('[kernel-host] fatal:', err);
  process.exit(1);
});
