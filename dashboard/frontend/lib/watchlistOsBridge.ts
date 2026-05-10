/**
 * Broadcasts thin watchlist_os Redis hints from the shared engine WebSocket to any subscriber.
 * Payloads carry user_id only — subscribers refetch snapshot-first GET /api/watchlist/operating.
 */

export type WatchlistOsHintPayload = { userId: number; kind: string };

type Listener = (p: WatchlistOsHintPayload) => void;

const listeners = new Set<Listener>();

export function subscribeWatchlistOsHint(cb: Listener): () => void {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

export function emitWatchlistOsHint(userId: number, kind: string): void {
  listeners.forEach((fn) => {
    try {
      fn({ userId, kind });
    } catch {
      /* ignore subscriber errors */
    }
  });
}
