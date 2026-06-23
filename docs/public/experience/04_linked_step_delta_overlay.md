# Linked-Step Delta Overlay Preview

## Problem

Linked requests can ask for a correction first and a recalculation afterward. The previous preview flow showed the first step's `before`/`after` sample rows, but downstream SELECT or aggregate steps still read the unchanged raw table. That made update-then-aggregate previews semantically stale even when each individual SQL statement passed validation.

## Direction

The workflow now treats linked-step corrections as preview deltas. An UPDATE preview builds row-level delta items from the full validated target set, while the screen still displays only a small sample. Raw DA/SA tables remain unchanged during preview. A downstream SELECT or aggregate receives the approved prior deltas as an effective preview context and reads from a raw-table overlay instead of the raw table alone.

## Schema

`006_linked_step_delta_overlay.sql` extends the linked plan with expiration/closure timestamps and adds `rule_engine_delta_item`. The table is keyed by linked plan and step metadata, stores raw row identity (`row_id`, `source_row_hash`), and keeps `before_json`, `after_json`, and `delta_json` for replaying the hypothetical state. Indexes are scoped by linked plan, row, step, and expiry so multiple sessions can share the same table without per-session temp tables.

## Runtime

The state contract now includes `active_step_id`, `accepted_step_ids`, `preview_delta_items`, and `effective_preview_context`. Intermediate linked-step approval is preview-only: it records/uses overlay deltas but does not execute the raw UPDATE. Downstream step previews are invalidated when an upstream approval or cancellation changes the overlay context.

## Verification Focus

The key acceptance case is an UPDATE step that changes a metric followed by an aggregate step over that same metric. The aggregate must reflect the overlay value, while raw DA/SA remains untouched until the final guarded execution path.
