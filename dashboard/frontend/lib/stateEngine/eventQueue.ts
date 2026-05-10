/**
 * Sequential WS message processing — one microtask drain; no interleaved applies.
 */

type Task = () => void;

const q: Task[] = [];
let scheduled = false;

export function getQueueDepth(): number {
  return q.length;
}

export function enqueueSequential(task: Task): void {
  q.push(task);
  if (scheduled) return;
  scheduled = true;
  queueMicrotask(() => {
    while (q.length) {
      const t = q.shift();
      if (t) {
        try {
          t();
        } catch {
          /* isolated task failure */
        }
      }
    }
    scheduled = false;
  });
}
