Compare the producer and the consumer of every contract the change touches:
API request/response shapes, DTOs, protobuf/JSON schemas, database columns,
events, feature flags, configuration keys, file formats.

Check: nullability and optional fields (a consumer that assumes presence);
enums and status values (a new value the old consumer maps to a default);
serialization and case conventions; units, currencies, precision and rounding;
time zones and date-only vs timestamp fields; pagination (cursor stability,
sorting ties, page size limits); versioning and compatibility of an old client
with the new server and a new client with the old server during rollout;
migrations (backfill, defaults, rollback, indexes for new predicates);
uniqueness and foreign-key constraints the code relies on but the schema does
not enforce, or vice versa; generated code that is out of date with its source.

A finding must name an observable contract violation: which field, which side,
which value, what breaks. An architectural preference is not a finding.
