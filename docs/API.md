# Webhook API

## Accept an event

`POST /api/events`, `Content-Type: application/json`.

Required headers:

- `X-Webhook-Timestamp`: Unix timestamp in whole seconds.
- `X-Webhook-Signature`: `sha256=` followed by the hexadecimal HMAC-SHA256 digest
  of `timestamp + "." + raw request body`, signed with `WEBHOOK_SECRET`.

Use `scripts/send_event.py` for an executable example. Body size is limited to
64 KiB. Timestamps outside ±300 seconds are rejected.

```json
{
  "id": "evt_example_1",
  "source": "release-api",
  "type": "release.created",
  "data": { "release_id": "rel_42", "title": "A fictional release" }
}
```

| Type              | Required data fields                                            |
| ----------------- | --------------------------------------------------------------- |
| `release.created` | `release_id`, `title`: nonempty strings, maximum 200 characters |
| `report.received` | `report_id`: string; `rows`: integer 0–1,000,000                |
| `catalog.updated` | `artist`: string; `tracks`: integer 0–1,000,000                 |

`id` and `source` are 1–128 identifier characters (letters, digits, `_`, `.`, `:`,
`-`), starting with a letter/digit. In demo mode, optional `simulate_failures`
accepts an integer 0–3. A nonzero value is rejected when demo mode is disabled.

| Status | Meaning                                      |
| ------ | -------------------------------------------- |
| 201    | New event committed                          |
| 200    | Identical duplicate; existing event returned |
| 400    | Malformed JSON                               |
| 401    | Invalid signature or timestamp               |
| 409    | Same source/ID with a different payload      |
| 413    | Body too large                               |
| 415    | Wrong media type                             |
| 422    | Invalid schema                               |

The response includes the internal `id`, `duplicate` flag and read API URL.
Acceptance does not mean the processor already succeeded.

## Admin read API

Send `Authorization: Bearer <ADMIN_TOKEN>` to:

| Method | Endpoint                                | Result                                  |
| ------ | --------------------------------------- | --------------------------------------- |
| GET    | `/api/events?status=all&source=&page=1` | 20 events per page                      |
| GET    | `/api/events/:id`                       | Event details and parsed payload        |
| GET    | `/metrics`                              | Prometheus text exposition              |
| GET    | `/health`                               | Public database health; no token needed |

Worker tick and requeue are authenticated **HTML form actions**, not JSON API
endpoints. They require the signed-in browser session and its CSRF token.

## Metrics

| Name                                       | Type          | Labels                          |
| ------------------------------------------ | ------------- | ------------------------------- |
| `hookwatch_events`                         | Gauge         | `status`                        |
| `hookwatch_attempts_total`                 | Counter       | `outcome` (`success` / `error`) |
| `hookwatch_process_duration_seconds_count` | Summary count | None                            |
| `hookwatch_process_duration_seconds_sum`   | Summary sum   | None                            |

No quantiles are exported. Prometheus can compute an average using rate(sum) /
rate(count) over a suitable interval. A scrape requires the admin bearer token.
