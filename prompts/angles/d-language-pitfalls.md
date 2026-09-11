Identify the language(s) and frameworks the diff touches, then scan it for the
classic pitfalls of each. Examples:

- JavaScript/TypeScript: `==` coercion, falsy zero, closure-captured loop
  variables, un-awaited promises, `forEach` with async callbacks, mutating
  shared state in reducers, optional chaining that hides a real error.
- Python: mutable default arguments, late-binding closures, `is` on values,
  `dict` iteration while mutating, `except:` swallowing `KeyboardInterrupt`,
  naive vs aware datetimes, `os.path` vs `pathlib` mixing.
- Go: nil map write, range-variable capture, `defer` inside loops, shadowed
  `err`, unchecked type assertions, context not propagated, goroutine leaks,
  `time.After` in select loops.
- Swift/Kotlin: force unwraps on optional data, retain cycles in closures,
  main-thread UI updates from background work, `Equatable` on the wrong fields.
- SQL and ORMs: string-built queries, missing `LIMIT` on user-driven scans,
  N+1 loops, `NULL` in `NOT IN`, missing index for the new predicate.
- Everywhere: float equality, integer overflow, time zones and DST, locale
  dependent formatting and parsing, character vs byte lengths, path joins with
  user input.

Flag any instance the diff introduces or leaves in a touched function.
