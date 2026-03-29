"""
app/services/ingestion.py

Incident ingestion service.

Handles parsing of uploaded files (CSV, JSON) and converting them into
IncidentInput objects. This is the layer between raw file bytes and
the normalizer — it deals with format-specific parsing and error handling.

The API routes call this service; the normalizer is called after ingestion.
"""

import json
import io
from typing import Optional
import pandas as pd

from app.schemas.incident import IncidentInput, CSVIncidentRow, BulkUploadResponse
from app.services.normalizer import normalize_from_csv_row, normalize_from_dict
from app.schemas.incident import NormalizedIncident
from app.utils.logger import get_logger

logger = get_logger(__name__)


# ── Required columns for CSV uploads ─────────────────────────────────────────
REQUIRED_CSV_COLUMNS = {"title"}
OPTIONAL_CSV_COLUMNS = {"description", "source", "service_name", "environment", "raw_severity"}
ALL_CSV_COLUMNS = REQUIRED_CSV_COLUMNS | OPTIONAL_CSV_COLUMNS


def parse_csv_upload(file_bytes: bytes) -> tuple[list[NormalizedIncident], list[str]]:
    """
    Parse a CSV file upload into a list of NormalizedIncident objects.

    Expected CSV columns (title is required, rest are optional):
        title, description, source, service_name, environment, raw_severity

    Args:
        file_bytes: Raw bytes of the uploaded CSV file.

    Returns:
        A tuple of (successful_incidents, error_messages).
        Rows that fail validation are skipped and their errors are collected.
    """
    incidents: list[NormalizedIncident] = []
    errors: list[str] = []

    try:
        df = pd.read_csv(io.BytesIO(file_bytes), dtype=str)
        df.columns = df.columns.str.strip().str.lower()
    except Exception as e:
        return [], [f"Failed to parse CSV file: {str(e)}"]

    # Validate required columns exist
    missing = REQUIRED_CSV_COLUMNS - set(df.columns)
    if missing:
        return [], [f"CSV is missing required columns: {missing}. Found: {list(df.columns)}"]

    # Drop any unrecognized extra columns silently
    valid_cols = [c for c in df.columns if c in ALL_CSV_COLUMNS]
    df = df[valid_cols]

    # Fill NaN with empty string so Pydantic doesn't choke on float NaN
    df = df.fillna("")

    logger.info(f"Parsing CSV upload: {len(df)} rows")

    for idx, row in df.iterrows():
        row_num = idx + 2  # +2 for 1-based + header row
        try:
            csv_row = CSVIncidentRow(**row.to_dict())
            normalized = normalize_from_csv_row(csv_row)
            incidents.append(normalized)
        except Exception as e:
            error_msg = f"Row {row_num} (title={row.get('title', 'N/A')!r}): {str(e)}"
            errors.append(error_msg)
            logger.warning(f"CSV row parse error: {error_msg}")

    logger.info(
        f"CSV ingestion complete: {len(incidents)} ok, {len(errors)} errors"
    )
    return incidents, errors


def parse_json_upload(file_bytes: bytes) -> tuple[list[NormalizedIncident], list[str]]:
    """
    Parse a JSON file upload into a list of NormalizedIncident objects.

    Accepts two JSON formats:
      - A JSON array: [{...}, {...}]
      - A single JSON object: {...}

    Args:
        file_bytes: Raw bytes of the uploaded JSON file.

    Returns:
        A tuple of (successful_incidents, error_messages).
    """
    incidents: list[NormalizedIncident] = []
    errors: list[str] = []

    try:
        raw = json.loads(file_bytes.decode("utf-8"))
    except Exception as e:
        return [], [f"Invalid JSON file: {str(e)}"]

    # Normalize to list
    if isinstance(raw, dict):
        records = [raw]
    elif isinstance(raw, list):
        records = raw
    else:
        return [], ["JSON must be an object or an array of objects."]

    logger.info(f"Parsing JSON upload: {len(records)} records")

    for idx, record in enumerate(records):
        if not isinstance(record, dict):
            errors.append(f"Record {idx + 1}: expected a JSON object, got {type(record).__name__}")
            continue
        try:
            normalized = normalize_from_dict(record, source="json")
            incidents.append(normalized)
        except Exception as e:
            title = record.get("title", "N/A")
            error_msg = f"Record {idx + 1} (title={title!r}): {str(e)}"
            errors.append(error_msg)
            logger.warning(f"JSON record parse error: {error_msg}")

    logger.info(
        f"JSON ingestion complete: {len(incidents)} ok, {len(errors)} errors"
    )
    return incidents, errors


def load_sample_incidents() -> list[NormalizedIncident]:
    """
    Load the built-in sample incidents from the data directory.
    Used by the UI to populate demo data with one click.

    Returns:
        List of NormalizedIncident objects from sample_incidents.json.
    """
    import os
    sample_path = os.path.join(
        os.path.dirname(__file__), "..", "data", "sample_incidents.json"
    )
    sample_path = os.path.abspath(sample_path)

    try:
        with open(sample_path, "r") as f:
            records = json.load(f)
        incidents = [normalize_from_dict(r, source="sample") for r in records]
        logger.info(f"Loaded {len(incidents)} sample incidents from {sample_path}")
        return incidents
    except Exception as e:
        logger.error(f"Failed to load sample incidents: {e}")
        return []
