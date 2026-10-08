# Cost accounting baseline

The phase-one ledger treats `BUDGET_DAILY_LIMIT` and
`ARK_IMAGE_COST_ESTIMATE` as CNY amounts. The example estimate is an operator
placeholder, not a vendor price guarantee; update it to the effective price in
your Volcengine contract before enabling production calls.

Each Ark HTTP attempt creates a SQLite row containing UTC request time, local
usage day, model, duration, status, estimated cost, and an error summary. Budget
is atomically reserved before the request. A failed attempt remains visible in
the attempt count but releases its cost reservation; a successful attempt keeps
the configured estimate.

