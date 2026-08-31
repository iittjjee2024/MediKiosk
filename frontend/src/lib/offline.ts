

const DB_NAME = "medikiosk";
const DB_VERSION = 1;
const STORE_QUEUE = "operation_queue";
const STORE_STATE = "session_state";

export type QueuedOp = {
  id: string;
  idempotencyKey: string;
  url: string;
  method: string;
  body: unknown;
  token: string;
  createdAt: number;
  attempts: number;
  lastError?: string;
  status: "pending" | "sent" | "failed" | "dead";
};

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE_QUEUE)) {
        db.createObjectStore(STORE_QUEUE, { keyPath: "id" });
      }
      if (!db.objectStoreNames.contains(STORE_STATE)) {
        db.createObjectStore(STORE_STATE, { keyPath: "key" });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function tx<T>(
  store: string,
  mode: IDBTransactionMode,
  fn: (s: IDBObjectStore) => IDBRequest<T>,
): Promise<T> {
  return openDb().then(
    (db) =>
      new Promise<T>((resolve, reject) => {
        const t = db.transaction(store, mode);
        const req = fn(t.objectStore(store));
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
      }),
  );
}

export function newKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `k-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export async function saveState(key: string, value: unknown): Promise<void> {
  if (typeof indexedDB === "undefined") return;
  await tx(STORE_STATE, "readwrite", (s) => s.put({ key, value }));
}

export async function loadState<T>(key: string): Promise<T | null> {
  if (typeof indexedDB === "undefined") return null;
  const row = await tx<{ key: string; value: T } | undefined>(
    STORE_STATE,
    "readonly",
    (s) => s.get(key),
  );
  return row ? row.value : null;
}

export async function clearAll(): Promise<void> {
  if (typeof indexedDB === "undefined") return;
  await tx(STORE_STATE, "readwrite", (s) => s.clear());
  await tx(STORE_QUEUE, "readwrite", (s) => s.clear());
}

export async function enqueue(
  op: Omit<QueuedOp, "id" | "attempts" | "status">,
): Promise<QueuedOp> {
  const row: QueuedOp = {
    ...op,
    id: newKey(),
    attempts: 0,
    status: "pending",
  };
  if (typeof indexedDB !== "undefined") {
    await tx(STORE_QUEUE, "readwrite", (s) => s.add(row));
  }
  return row;
}

export async function pending(): Promise<QueuedOp[]> {
  if (typeof indexedDB === "undefined") return [];
  const all = await tx<QueuedOp[]>(STORE_QUEUE, "readonly", (s) =>
    s.getAll(),
  );
  return all
    .filter((o) => o.status === "pending" || o.status === "failed")
    .sort((a, b) => a.createdAt - b.createdAt);
}

export async function update(op: QueuedOp): Promise<void> {
  if (typeof indexedDB === "undefined") return;
  await tx(STORE_QUEUE, "readwrite", (s) => s.put(op));
}

export async function pendingCount(): Promise<number> {
  return (await pending()).length;
}

const MAX_ATTEMPTS = 10;

function backoffMs(attempt: number): number {
  const base = Math.min(60_000, 1000 * 2 ** attempt);
  return base / 2 + Math.random() * (base / 2);
}

export type DrainResult = { sent: number; failed: number; dead: number };

export async function drain(): Promise<DrainResult> {
  const result: DrainResult = { sent: 0, failed: 0, dead: 0 };
  if (typeof navigator !== "undefined" && !navigator.onLine) return result;

  for (const op of await pending()) {
    try {
      const res = await fetch(op.url, {
        method: op.method,
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${op.token}`,
          "Idempotency-Key": op.idempotencyKey,
        },
        body: JSON.stringify(op.body),
      });

      if (res.ok) {
        op.status = "sent";
        result.sent += 1;
      } else if (res.status >= 400 && res.status < 500) {
        op.status = "dead";
        op.lastError = `${res.status} ${await res.text()}`.slice(0, 500);
        result.dead += 1;
      } else {
        op.attempts += 1;
        op.status = op.attempts >= MAX_ATTEMPTS ? "dead" : "failed";
        op.lastError = `${res.status}`;
        if (op.status === "dead") result.dead += 1;
        else result.failed += 1;
        await update(op);
        await new Promise((r) => setTimeout(r, backoffMs(op.attempts)));
        continue;
      }
    } catch (err) {
      op.attempts += 1;
      op.status = op.attempts >= MAX_ATTEMPTS ? "dead" : "failed";
      op.lastError = String(err).slice(0, 500);
      if (op.status === "dead") result.dead += 1;
      else result.failed += 1;
    }
    await update(op);
  }
  return result;
}
