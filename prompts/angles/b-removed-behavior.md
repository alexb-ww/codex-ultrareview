For every line the diff DELETES or replaces, name the invariant or behaviour it
enforced, then search the new code for where that invariant is re-established.
If you cannot find it, that is a candidate: a removed guard or null check, a
dropped error path, a narrowed validation, a removed retry or timeout, a lost
lock or transaction boundary, a regex or allowlist that lost an anchor, a
default that flipped, a log or metric that used to reveal failure, a deleted or
weakened test that covered a real case.

Read the base version of each touched file (`git show <base>:<path>`) so that you
compare behaviour, not just text. Moved code counts as deleted plus added: check
that what moved kept its preconditions in the new place.
