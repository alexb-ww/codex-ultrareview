Read every hunk in the diff line by line. Then read the whole enclosing function
of each hunk: bugs on unchanged lines of a touched function are in scope, since
the change re-exposes them or fails to fix them. For every line ask: what input,
state, timing or platform makes this line wrong?

Look for: inverted or wrong conditions; off-by-one on ranges, slices and loops;
null/nil/undefined dereference where a nearby line shows the value can be
absent; missing `await`, missing `return`, missing `else`; falsy-zero or
empty-string treated as missing; wrong variable from copy-paste (`a` where `b`
was meant); an error swallowed in a catch that should propagate; unescaped regex
metacharacters; integer division, rounding and overflow; time-zone and DST
drift; comparisons of the wrong types; a changed default argument; a resource
opened and not closed on the error path.
