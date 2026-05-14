# LoRA adapter registry

One row per trained adapter. PII-filter version is the
`PII_FILTER_VERSION` constant from `src/toybox/ai/redact.py` at the time
the training corpus was exported. When the filter changes (e.g. new
regex, new deny-list source), bump the version and retrain.

Trained adapter directories live alongside this README as
`data/models/lora/<YYYY-MM-DDTHHMMSS>/` and are gitignored.

| adapter | base | training_data_since | training_data_until | row_count | pii_filter_version | measured_delta_summary | deployed_at | active |
|---|---|---|---|---|---|---|---|---|
| _(empty — first adapter lands with Phase E Step 27 full ship)_ | | | | | | | | |

## Operator: flag a row as PII-unredactable

When spot-checking a labeled_events row reveals PII the regex/deny-list didn't catch
(unusual address format, ASR-mangled name, etc.), flag the row instead of trying to
patch the redactor for a single case:

```bash
sqlite3 data/toybox.db "UPDATE labeled_events SET redact_for_sft=1 WHERE id=<row_id>;"
```

Once the redactor changes structurally (new pattern, new deny-list source), bump
`PII_FILTER_VERSION` in `src/toybox/ai/redact.py` and add a new row to this registry
when the next training corpus is exported under that version.
