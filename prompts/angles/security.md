Check authentication and authorization separately: is the caller identified,
and is this caller allowed to act on this object? Look for object ownership and
tenant isolation (an id taken from the request and used without checking it
belongs to the caller's company/account), privilege boundaries (admin-only paths
reachable by ordinary roles), and session lifecycle (invalidation on password
change, logout, role change).

Trace untrusted input from source to sink: injections (SQL, shell, template,
header, log), path traversal and file access, SSRF and open redirects, unsafe
deserialization, mass assignment, secrets or tokens written to logs, responses
or analytics, webhook signature checks, rate limits on expensive or sensitive
operations, cryptographic misuse (predictable tokens, weak comparisons).

Read middleware and upstream validation before deciding: a check may live in an
interceptor. A dependency concern needs the exact version from the lockfile and
an affected code path; a name alone is not a finding. Never contact external
systems; reason from the code.
