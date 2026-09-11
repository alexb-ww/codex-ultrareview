Look for state that can be observed or written by more than one actor at a
time: concurrent requests, background jobs, retries, message redelivery,
timers, UI navigation racing an in-flight response, two tabs, two instances.

For each shared piece of state check: check-then-act sequences without a lock or
a conditional write; lost updates (read, modify, write of a whole row or
document); idempotency of handlers that can be delivered twice; transactions
that do not cover every write of a logical operation, or that hold locks across
network calls; ordering assumptions between events that can arrive out of
order; cancellation and timeouts that leave state half-changed; caches that are
updated before or without the source of truth; singletons and module-level
mutable state; lifecycle races (callback after dispose, subscription after
unsubscribe).

For every race you report, give a concrete schedule of operations (A reads, B
writes, A writes) and explain why the existing locks, isolation level or
uniqueness constraints allow it. A possible race with no reachable concurrent
caller is still worth reporting as a candidate; the verifier will decide.
