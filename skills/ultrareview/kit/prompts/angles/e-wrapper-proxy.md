When the change adds or modifies a type that wraps another (cache, proxy,
decorator, adapter, facade, repository over a client, middleware, view-model
over a store), check that every method routes to the wrapped instance and not
back through a registry, session or global. A caching provider whose `delegate`
resolves ids via `session.get(...)` instead of `delegate.get(...)` re-enters the
cache or recurses; a retry wrapper that calls the public entry point instead of
the inner call retries the retries.

Also check: the wrapper forwards every method its callers actually use
(grep the callers); error translation keeps the information callers branch on;
identity, equality and hashing are consistent with the wrapped object where
collections depend on it; lifecycle (open/close, subscribe/unsubscribe,
start/stop) is forwarded in both directions; concurrency guarantees of the
wrapped object are not silently weakened by the wrapper.
