"""Publication artifacts: data dictionary, dataset card, and FAIR metadata.

These make a run *citable and discoverable* by the scientific community:

* a data dictionary generated from the pydantic schema (always in sync),
* a human-readable dataset card,
* a Croissant (MLCommons) JSON-LD metadata file so the dataset is machine-
  discoverable on data repositories that index Croissant.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from . import config
from .models import (
    EQRComplianceFinding,
    EQRPerformanceImprovementProject,
    EQRQualityMeasure,
)

# record models that compose the EQR dataset, in table order
EQR_MODELS = [EQRQualityMeasure, EQRPerformanceImprovementProject, EQRComplianceFinding]


def write_data_dictionary(out_dir: Path, models: list[type] | None = None) -> Path:
    models = models or EQR_MODELS
    lines = ["# Data Dictionary", ""]
    lines.append(
        "Generated from the schema. Every table also carries provenance columns "
        "(`prov_*`: `source_document_id`, `source_url`, `page_start`, `page_end`, "
        "`method`, `model_name`, `confidence`, `extracted_at`) and QA columns "
        "(`qa_status`, `qa_flags`).\n"
    )
    for model in models:
        schema = model.model_json_schema()
        lines.append(f"## {model.__name__}")
        doc = (model.__doc__ or "").strip().splitlines()
        if doc:
            lines.append(f"_{doc[0]}_\n")
        lines.append("| field | type | description |")
        lines.append("|---|---|---|")
        for name, prop in schema.get("properties", {}).items():
            if name in {"provenance", "qa_status", "qa_flags", "record_type"}:
                continue
            typ = prop.get("type") or _ref_name(prop)
            desc = prop.get("description", "")
            lines.append(f"| `{name}` | {typ} | {desc} |")
        lines.append("")
    path = out_dir / "DATA_DICTIONARY.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _ref_name(prop: dict[str, Any]) -> str:
    if "$ref" in prop:
        return prop["$ref"].split("/")[-1]
    if "anyOf" in prop:
        # join with "or" (not "|") so it doesn't break the markdown table column
        return " or ".join(_ref_name(p) if "$ref" in p else (p.get("type") or "?") for p in prop["anyOf"])
    if "allOf" in prop:
        return _ref_name(prop["allOf"][0])
    return "string"


def write_dataset_card(
    out_dir: Path, dataset_meta: dict[str, Any], summary: dict[str, Any]
) -> Path:
    name = dataset_meta.get("name", dataset_meta.get("id"))
    lines = [
        f"# Dataset Card: {name}",
        "",
        f"- **Version:** {config.__version__}",
        f"- **Built:** {date.today().isoformat()}",
        f"- **Source documents:** {summary.get('n_documents', 0)}",
        f"- **License (derived data):** {dataset_meta.get('license', 'public-record / CC0-1.0')}",
        "",
        "## Description",
        dataset_meta.get("description", ""),
        "",
        "## Tables",
    ]
    for table, n in summary.get("tables", {}).items():
        lines.append(f"- `{table}` — {n} rows")
    lines += [
        "",
        "## Provenance & QA",
        "Every row links to its source document (`prov_source_document_id`, "
        "`prov_source_url`, `prov_page_start`) and records how it was extracted "
        "(`prov_method`, `prov_model_name`, `prov_confidence`). Filter on "
        "`qa_status` (`pass`/`warn`/`fail`) and inspect `qa_flags` before analysis.",
        "",
        "## How to cite",
        "See `CITATION.cff` in the repository root.",
        "",
        "> Derived from public-record documents. This dataset reorganizes public "
        "information; it contains no protected health information (PHI).",
    ]
    path = out_dir / "DATASET_CARD.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_croissant(
    out_dir: Path, dataset_meta: dict[str, Any], summary: dict[str, Any]
) -> Path:
    """Minimal Croissant (MLCommons) JSON-LD describing the produced CSVs."""
    distribution = []
    record_sets = []
    for table in summary.get("tables", {}):
        file_id = f"{table}.csv"
        distribution.append(
            {
                "@type": "cr:FileObject",
                "@id": file_id,
                "name": file_id,
                "encodingFormat": "text/csv",
                "contentUrl": file_id,
            }
        )
        record_sets.append(
            {
                "@type": "cr:RecordSet",
                "@id": table,
                "name": table,
                "field": [
                    {
                        "@type": "cr:Field",
                        "@id": f"{table}/source_document_id",
                        "dataType": "sc:Text",
                        "source": {"fileObject": {"@id": file_id}, "extract": {"column": "prov_source_document_id"}},
                    }
                ],
            }
        )
    croissant = {
        "@context": {
            "@vocab": "https://schema.org/",
            "cr": "http://mlcommons.org/croissant/",
            "sc": "https://schema.org/",
        },
        "@type": "sc:Dataset",
        "name": dataset_meta.get("name", dataset_meta.get("id")),
        "description": dataset_meta.get("description", ""),
        "license": dataset_meta.get("license", "https://creativecommons.org/publicdomain/zero/1.0/"),
        "version": config.__version__,
        "datePublished": date.today().isoformat(),
        "distribution": distribution,
        "recordSet": record_sets,
    }
    path = out_dir / "croissant.json"
    path.write_text(json.dumps(croissant, indent=2), encoding="utf-8")
    return path
