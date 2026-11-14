---
trigger: always_on
---

Unit tests must never sleep or depend on the wall clock. Inject a clock and advance it
explicitly, so a test cannot pass or fail because of machine speed or time of day.
