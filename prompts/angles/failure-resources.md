Follow every path where something goes wrong: a dependency times out, returns
an error status or malformed data; a write succeeds and the next one fails; the
process is interrupted; the queue is full; the disk is full.

Check: errors swallowed, logged and forgotten, or converted to a success value;
non-2xx HTTP or non-OK RPC statuses treated as data; partial failure without
rollback or compensation; retries without backoff, without idempotency, or on
non-retryable errors; missing or infinite timeouts; resources (connections,
files, subscriptions, goroutines/tasks, listeners, timers) not released on the
error path or on cancellation; unbounded growth (caches without eviction,
queues without limits, in-memory accumulation per request); blocking calls on
hot paths or startup; shutdown that drops in-flight work.

For performance, give the input size, frequency or measurement that makes it
matter; an extra loop by itself is not a finding. Distinguish a missing log
line from lost user data; only the second is a defect in this angle.
