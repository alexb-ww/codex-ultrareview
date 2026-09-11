For each function, method, type, constant or schema the diff changes, find its
users (grep for the symbol across the repository, including tests, templates and
configuration) and check whether the change breaks any of them: a new
precondition the caller does not meet, a changed return shape or nullability, a
new exception or error code, a changed unit or default, a timing or ordering
dependency, a renamed field a consumer still reads by the old name.

Also trace the other direction: what the changed code now calls. Does a parallel
change in the same diff make a call unsafe (a helper that now expects a
non-empty list, a repository method that now requires a transaction)? For data
flowing across a boundary (API, event, database row, file), check both the
producer and the consumer in the state this change leaves them.
