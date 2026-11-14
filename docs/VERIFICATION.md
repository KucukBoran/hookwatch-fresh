# Testing

Verified on **2026-11-14**, using Python **3.12.14**, Flask **3.1.3** and
Gunicorn **27.0.1** on Linux.

| Check | Observed result |
| --- | --- |
| `python -m pytest` | 19 tests passed |
| Python module compilation | Passed |
| Gunicorn HTTP health check | Passed with one worker and four threads |
| Signed CLI request over HTTP | 201 for a new event; 200 for the identical repeated event |
| Desktop browser, 1440 px | Dashboard, filters and event detail rendered |
| Mobile browser, 390 px | No document-level horizontal overflow |
| Browser JavaScript errors | None observed |
| Configuration parsing | JSON and YAML files parsed successfully |

Tests cover exact-body signatures, expired timestamps, schema validation,
oversized bodies, concurrent duplicates, conflicting reused IDs, source scoping,
backoff, failed-event requeue, expired leases, stale completions, authentication,
CSRF, output escaping and authenticated metrics.

## Validation scope

The CI workflow targets Ubuntu with Python 3.12. Container builds, live Prometheus
scraping, Grafana provisioning and native Windows execution are outside the
verified test matrix.

## GitHub Actions

The Checks workflow runs on pushes and pull requests.
[View workflow results](https://github.com/KucukBoran/hookwatch/actions/workflows/ci.yml).
The README badge displays the latest workflow status.
