Check whether the tests around the change test real behaviour. Read the test
files the diff touches and the existing tests of the changed modules.

Look for: assertions that cannot fail (asserting the mock's own return value,
`assert result` on a non-empty object, snapshot tests regenerated wholesale);
mocks and fakes that hide the defect (a fake repository that accepts what the
real schema rejects, a stubbed clock that never crosses a boundary, a mocked
HTTP client that never returns an error status); tests deleted, skipped or
weakened by the change; fixtures that share state between tests; test
configuration that differs from the build (different flags, environment,
database engine); CI jobs that do not run the tests that would catch this
change; feature flags and environments where the changed code path is never
exercised by any test.

Also look at integration seams the change crosses: module A now assumes module
B behaves differently, but B's tests still pin the old behaviour, or vice versa.
A missing test is a coverage note, not a defect; a test that passes while the
behaviour is wrong is a defect candidate. Do not modify tests, snapshots or
lockfiles.
