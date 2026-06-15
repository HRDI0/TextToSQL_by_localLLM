from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

try:
    from load_da_sa_tables import canonical_header as import_script_canonical_header  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - defensive fallback for incomplete environments
    import_script_canonical_header = None

def canonicalize_with_import_rules(value: str) -> str:
    if import_script_canonical_header is None:
        return value
    return import_script_canonical_header(value)


def resolve_column_from_import_rules(field: str, table_name: str, table_columns: dict[str, list[str]]) -> str:
    if "." in field:
        prefix, raw_column = field.split(".", 1)
        if prefix.strip("`") == table_name:
            field = raw_column.strip("`")
    columns = table_columns.get(table_name, [])
    if field in columns:
        return field
    canonical = canonicalize_with_import_rules(field)
    if canonical in columns:
        return canonical
    return field


def normalized_token(value: str) -> str:
    return "".join(ch.lower() for ch in value if ch.isalnum())


def source_channel_candidates_from_import_rules(
    text: str,
    source_channel_values: dict[str, list[str]],
    table_names: list[str],
    source_channel_mappings: list[dict[str, object]] | None = None,
) -> list[str]:
    text_token = normalized_token(text)
    candidates: set[str] = set()
    for table_name in table_names:
        for source_channel in source_channel_values.get(table_name, []):
            source_token = normalized_token(str(source_channel))
            if source_token and source_token in text_token:
                candidates.add(str(source_channel))
        live_values = set(source_channel_values.get(table_name, []))
        for item in source_channel_mappings or []:
            if str(item.get("target_table")) != table_name:
                continue
            user_term_token = normalized_token(str(item.get("user_term", "")))
            source_channel = str(item.get("source_channel", ""))
            if user_term_token and user_term_token in text_token and source_channel in live_values:
                candidates.add(source_channel)
    return sorted(candidates)
