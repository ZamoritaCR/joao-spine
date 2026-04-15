from __future__ import annotations

import copy
import csv
import ftplib
import hashlib
import io
import json
import logging
import os
import re
import shutil
import tempfile
import time
import uuid
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

load_dotenv(Path.home() / "joao-spine" / ".env", override=False)
load_dotenv(Path.home() / ".env.drdata", override=False)
load_dotenv(Path.home() / ".env", override=False)

LOG = logging.getLogger("drdata-v3")
logging.basicConfig(level=logging.INFO)

V1 = "/home/zamoritacr/taop-repos/dr-data"
import sys

if V1 not in sys.path:
    sys.path.insert(0, V1)

from core.audit_engine import AuditEngine
from core.correction_store import get_correction_stats, lookup_corrections, store_correction
from core.enhanced_tableau_parser import get_xml_root, parse_twb
from core.pipeline_state import PipelineState, StageName, StageStatus
from core.powerbi_publisher import (
    FABRIC_API,
    FABRIC_SCOPE,
    PBI_API,
    PBI_SCOPE,
    delete_item,
    execute_dax_query,
    get_access_token,
    get_report_pages,
    publish_pbip,
)
from core.qa_agent import QAAgent
from core.synthetic_data import generate_from_tableau_spec

try:
    from core.transpiler import translate_formula as v1_translate_formula
except Exception:
    v1_translate_formula = None

try:
    from core.multi_brain import dispatch_multi_brain
except Exception:
    dispatch_multi_brain = None

try:
    from core.correction_store import load_session as supabase_load_session
except Exception:
    supabase_load_session = None

try:
    from core.correction_store import save_session as supabase_save_session
except Exception:
    supabase_save_session = None

try:
    import pandas as pd
except Exception:
    pd = None

try:
    from openai import OpenAI
except Exception:
    OpenAI = None


WORKSPACE_ID = "226a11c9-8f9a-4374-b4c6-5e01dafa482d"
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-2025-04-14")
STAGE_NAMES = [
    "PARSE",
    "FIELD_MAPPING",
    "FORMULA_TRANSLATION",
    "VISUAL_MAPPING",
    "DATA_MODEL",
    "SYNTHETIC_DATA",
    "PBIP_BUILD",
    "QA_GATE",
    "PUBLISH_AND_PACKAGE",
]
EDITABLE_FIELDS = {
    0: ["fields[].name", "data_sources[].name", "fields[].ignored"],
    1: ["fields[].pbi_name", "fields[].semantic_type", "fields[].tier", "fields[].note"],
    2: ["formulas[].dax", "formulas[].notes"],
    3: ["sheets[].pbi_visual_type", "sheets[].bindings"],
    4: ["tables[].name", "relationships[].cardinality"],
    5: ["num_rows", "schema[].type_override"],
    6: ["manifest_review"],
    7: ["override_with_warnings", "send_back_to_stage"],
    8: [],
}


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def slugify(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", value or "").strip("_")
    return cleaned or "drdata"


def titleize(value: str) -> str:
    text = re.sub(r"[_\s]+", " ", value or "").strip()
    return text.title() if text else "Data"


def to_snake_case(value: str) -> str:
    value = re.sub(r"[\[\]`\"]", "", value or "")
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    value = re.sub(r"[^A-Za-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_").lower()
    return value or "unnamed_field"


def deep_copy_jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def safe_json_dumps(value: Any, indent: int = 2) -> str:
    return json.dumps(value, indent=indent, default=str, ensure_ascii=True)


def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


@dataclass
class SessionState:
    session_id: str
    created_at: str
    twbx_path: str
    workbook_name: str
    stage: int
    stage_history: List[dict]
    stage_data: Dict[int, dict]
    corrections: Dict[int, dict]
    approved: List[int]
    chat_history: List[dict]
    pbi_report_id: str
    pbi_workspace_id: str
    zip_path: str
    status: str
    parse_cache: Dict[str, Any] = field(default_factory=dict)
    dataframes: Dict[int, str] = field(default_factory=dict)
    pipeline_state: Dict[str, Any] = field(default_factory=dict)


_sessions: Dict[str, SessionState] = {}


class EditRequest(BaseModel):
    field_path: str
    original_value: Any = None
    corrected_value: Any = None


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)


app = FastAPI(title="Dr. Data API v3", version="3.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def get_openai_client() -> Optional[OpenAI]:
    if OpenAI is None or not os.getenv("OPENAI_API_KEY"):
        return None
    return OpenAI(api_key=os.environ["OPENAI_API_KEY"])


def get_session_or_404(session_id: str) -> SessionState:
    session = _sessions.get(session_id)
    if session:
        return session
    if supabase_load_session:
        restored = supabase_load_session(session_id)
        if restored:
            session = SessionState(
                session_id=session_id,
                created_at=restored.get("created_at", utcnow_iso()),
                twbx_path=restored.get("twbx_filename", ""),
                workbook_name=restored.get("twbx_filename", ""),
                stage=int(restored.get("current_stage", 0)),
                stage_history=restored.get("pipeline_state", {}).get("stage_history", []),
                stage_data={int(k): v for k, v in (restored.get("pipeline_state", {}).get("stage_data", {}) or {}).items()},
                corrections={int(k): v for k, v in (restored.get("pipeline_state", {}).get("corrections", {}) or {}).items()},
                approved=restored.get("pipeline_state", {}).get("approved", []),
                chat_history=restored.get("pipeline_state", {}).get("chat_history", []),
                pbi_report_id=restored.get("pipeline_state", {}).get("pbi_report_id", ""),
                pbi_workspace_id=restored.get("pipeline_state", {}).get("pbi_workspace_id", WORKSPACE_ID),
                zip_path=restored.get("pipeline_state", {}).get("zip_path", ""),
                status=restored.get("pipeline_state", {}).get("status", "active"),
            )
            _sessions[session_id] = session
            return session
    raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")


def persist_session(session: SessionState) -> None:
    pipeline = {
        "stage_history": session.stage_history,
        "stage_data": session.stage_data,
        "corrections": session.corrections,
        "approved": session.approved,
        "chat_history": session.chat_history,
        "pbi_report_id": session.pbi_report_id,
        "pbi_workspace_id": session.pbi_workspace_id,
        "zip_path": session.zip_path,
        "status": session.status,
    }
    session.pipeline_state = pipeline
    if supabase_save_session:
        try:
            supabase_save_session(
                session.session_id,
                {
                    "twbx_filename": session.twbx_path,
                    "pipeline_state": pipeline,
                    "current_stage": session.stage,
                    "tableau_spec": session.parse_cache.get("raw_spec", {}),
                    "data_profile": session.stage_data.get(5, {}),
                    "translations": session.stage_data.get(2, {}),
                    "config": session.stage_data.get(6, {}),
                },
            )
        except Exception as exc:
            LOG.warning("Supabase session save failed: %s", exc)


def snapshot_stage(session: SessionState, stage_num: int, label: str = "snapshot") -> None:
    current = session.stage_data.get(stage_num)
    if current is None:
        return
    session.stage_history.append(
        {
            "stage": stage_num,
            "label": label,
            "timestamp": utcnow_iso(),
            "data": deep_copy_jsonable(current),
        }
    )


def resolve_path_tokens(field_path: str) -> List[Any]:
    tokens: List[Any] = []
    for chunk in field_path.split("."):
        if not chunk:
            continue
        match = re.match(r"^([^\[]+)", chunk)
        if match:
            tokens.append(match.group(1))
        for index in re.findall(r"\[(\d+)\]", chunk):
            tokens.append(int(index))
    return tokens


def get_path_value(container: Any, field_path: str) -> Any:
    value = container
    for token in resolve_path_tokens(field_path):
        if isinstance(token, int):
            value = value[token]
        else:
            value = value[token]
    return value


def set_path_value(container: Any, field_path: str, value: Any) -> None:
    tokens = resolve_path_tokens(field_path)
    target = container
    for token in tokens[:-1]:
        if isinstance(token, int):
            target = target[token]
        else:
            if token not in target:
                target[token] = {}
            target = target[token]
    last = tokens[-1]
    if isinstance(last, int):
        target[last] = value
    else:
        target[last] = value


def summarize_counts(values: Iterable[dict], key: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for value in values:
        item = str(value.get(key, "unknown"))
        counts[item] = counts.get(item, 0) + 1
    return counts


def calc_complexity(field_count: int, sheet_count: int, calc_count: int) -> str:
    score = field_count + (sheet_count * 4) + (calc_count * 5)
    if score < 25:
        return "Low"
    if score < 70:
        return "Medium"
    if score < 130:
        return "High"
    return "Very High"


def normalize_stage0(raw_spec: dict) -> dict:
    fields: List[dict] = []
    data_source_name_by_table: Dict[str, str] = {}
    for ds in raw_spec.get("datasources", []):
        ds_name = ds.get("caption") or ds.get("name") or "Data Source"
        for table in ds.get("tables", []) or []:
            data_source_name_by_table[table] = ds_name
        for col in ds.get("columns", []):
            name = col.get("caption") or col.get("name") or "Unnamed Field"
            formula = col.get("formula", "")
            fields.append(
                {
                    "name": name,
                    "data_type": col.get("datatype", "string"),
                    "role": col.get("role", "dimension"),
                    "formula": formula,
                    "source_table": ds.get("tables", ["Data"])[0] if ds.get("tables") else ds_name,
                    "data_source": ds_name,
                    "ignored": False,
                }
            )

    existing_names = {field["name"] for field in fields}
    for calc in raw_spec.get("calculated_fields", []):
        name = calc.get("name") or calc.get("caption") or calc.get("internal_name") or "Calculated Field"
        if name in existing_names:
            continue
        fields.append(
            {
                "name": name,
                "data_type": calc.get("datatype", "real"),
                "role": calc.get("role", "measure"),
                "formula": calc.get("formula", ""),
                "source_table": calc.get("datasource", "Data"),
                "data_source": calc.get("datasource", "Data"),
                "ignored": False,
            }
        )

    sheets: List[dict] = []
    for ws in raw_spec.get("worksheets", []):
        rows_fields = ws.get("rows_fields") or ([ws.get("rows")] if ws.get("rows") else [])
        cols_fields = ws.get("cols_fields") or ([ws.get("cols")] if ws.get("cols") else [])
        sheets.append(
            {
                "name": ws.get("name", "Worksheet"),
                "mark_type": ws.get("mark_type") or ws.get("chart_type") or "automatic",
                "rows_field": rows_fields[0] if rows_fields else "",
                "cols_field": cols_fields[0] if cols_fields else "",
                "rows_fields": rows_fields,
                "cols_fields": cols_fields,
                "dimensions": ws.get("dimensions", []),
                "measures": ws.get("measures", []),
                "filters": ws.get("filters", []),
                "worksheet_colors": raw_spec.get("worksheet_colors", {}).get(ws.get("name", ""), {}),
            }
        )

    data_sources = []
    for ds in raw_spec.get("datasources", []):
        data_sources.append(
            {
                "name": ds.get("caption") or ds.get("name") or "Data Source",
                "connection_type": ds.get("connection_type", "unknown"),
                "tables": ds.get("tables", []),
            }
        )

    calc_count = sum(1 for field in fields if field.get("formula"))
    field_count = len(fields)
    sheet_count = len(sheets)
    return {
        "summary": {
            "field_count": field_count,
            "sheet_count": sheet_count,
            "calc_count": calc_count,
            "complexity": calc_complexity(field_count, sheet_count, calc_count),
        },
        "fields": fields,
        "sheets": sheets,
        "data_sources": data_sources,
        "raw_spec": raw_spec,
        "data_source_name_by_table": data_source_name_by_table,
    }


def classify_semantic_type(field: dict) -> str:
    formula = str(field.get("formula", "")).strip()
    role = str(field.get("role", "")).lower()
    data_type = str(field.get("data_type", "")).lower()
    name = str(field.get("name", "")).lower()
    if formula:
        return "calculated"
    if "date" in data_type or any(token in name for token in ["date", "day", "month", "year", "quarter"]):
        return "date"
    if role == "measure" or data_type in {"integer", "real", "float", "double", "numeric"}:
        return "measure"
    return "dimension"


def mapping_tier(confidence: int, semantic_type: str, field: dict) -> str:
    if field.get("ignored"):
        return "BLOCKED"
    if semantic_type == "calculated":
        return "GOOD" if confidence >= 80 else "REVIEW"
    if confidence >= 92:
        return "AUTO"
    if confidence >= 78:
        return "GOOD"
    if confidence >= 60:
        return "REVIEW"
    return "MANUAL"


def calc_confidence(field: dict, semantic_type: str, corrections: List[dict]) -> int:
    score = 55
    if semantic_type in {"dimension", "measure"}:
        score += 20
    if semantic_type == "date":
        score += 18
    if semantic_type == "calculated":
        score += 10
    if field.get("formula"):
        score -= 4
    if corrections:
        score += 12
    if field.get("name") and to_snake_case(field["name"]) != "unnamed_field":
        score += 8
    return max(5, min(99, score))


def translate_formula_safe(tableau_formula: str, table_name: str, columns: List[str]) -> dict:
    if v1_translate_formula is not None:
        try:
            result = v1_translate_formula(tableau_formula)
            if isinstance(result, dict):
                return {
                    "dax": result.get("dax", ""),
                    "confidence": int(float(result.get("confidence", 0.8)) * 100) if result.get("confidence", 0) <= 1 else int(result.get("confidence", 0)),
                    "tier": result.get("tier", "AUTO"),
                    "method": result.get("method", "deterministic"),
                }
        except Exception as exc:
            LOG.warning("core.transpiler translate failed: %s", exc)
    if dispatch_multi_brain is not None:
        try:
            result = dispatch_multi_brain(tableau_formula, table_name=table_name or "Data", columns=columns or [])
            consensus = result.get("consensus", {})
            tier = "AUTO" if consensus.get("confidence", 0) >= 0.9 else "REVIEW"
            return {
                "dax": consensus.get("dax", ""),
                "confidence": int(consensus.get("confidence", 0) * 100),
                "tier": tier,
                "method": f"multi_brain:{consensus.get('winner', 'unknown')}",
            }
        except Exception as exc:
            LOG.warning("multi_brain translate failed: %s", exc)
    return heuristic_formula_translation(tableau_formula)


def heuristic_formula_translation(tableau_formula: str) -> dict:
    formula = (tableau_formula or "").strip()
    dax = formula
    replacements = [
        (r"\bIF\b", "IF"),
        (r"\bTHEN\b", ","),
        (r"\bELSEIF\b", ","),
        (r"\bELSE\b", ","),
        (r"\bEND\b", ")"),
        (r"\bZN\(", "COALESCE("),
        (r"\bSUM\(", "SUM("),
        (r"\bAVG\(", "AVERAGE("),
        (r"\bCOUNTD\(", "DISTINCTCOUNT("),
    ]
    for pattern, replacement in replacements:
        dax = re.sub(pattern, replacement, dax, flags=re.IGNORECASE)
    dax = re.sub(r"\[(.+?)\]", lambda m: f"[{titleize(to_snake_case(m.group(1)))}]", dax)
    blocked = any(keyword in formula.lower() for keyword in ["lod", "fixed", "include", "exclude", "table calculation", "window_"])
    return {
        "dax": dax,
        "confidence": 40 if blocked else 72,
        "tier": "BLOCKED" if blocked else "REVIEW",
        "method": "heuristic",
    }


def run_openai_json(prompt: str, system_prompt: str) -> Optional[dict]:
    client = get_openai_client()
    if client is None:
        return None
    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
        )
        content = response.choices[0].message.content or "{}"
        return json.loads(content)
    except Exception as exc:
        LOG.warning("OpenAI JSON call failed: %s", exc)
        return None


def run_openai_text(system_prompt: str, user_prompt: str) -> str:
    client = get_openai_client()
    if client is None:
        raise RuntimeError("OPENAI_API_KEY not configured")
    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        temperature=0.2,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    return response.choices[0].message.content or ""


def compute_stage0(session: SessionState) -> dict:
    if not session.twbx_path:
        raise HTTPException(status_code=400, detail="No workbook uploaded for this session")
    raw_spec = parse_twb(session.twbx_path)
    normalized = normalize_stage0(raw_spec)
    session.parse_cache = normalized
    session.stage_data[0] = normalized
    session.stage = 0
    persist_session(session)
    return normalized


def compute_stage1(session: SessionState) -> dict:
    source = session.stage_data.get(0)
    if not source:
        raise HTTPException(status_code=400, detail="Stage 0 data is missing")
    fields = []
    for idx, field in enumerate(source.get("fields", [])):
        corrections = lookup_corrections("field_mapping", field_path=field.get("name", ""), limit=5)
        semantic_type = classify_semantic_type(field)
        confidence = calc_confidence(field, semantic_type, corrections)
        pbi_name = to_snake_case(field.get("name", ""))
        tier = mapping_tier(confidence, semantic_type, field)
        mapping = {
            "field_name": field.get("name", ""),
            "source_table": field.get("source_table", "Data"),
            "semantic_type": semantic_type,
            "pbi_name": pbi_name,
            "tier": tier,
            "confidence": confidence,
            "formula": field.get("formula", ""),
            "data_type": field.get("data_type", "string"),
            "note": "",
            "ignored": bool(field.get("ignored")),
            "examples_used": len(corrections),
            "index": idx,
        }
        if tier in {"REVIEW", "MANUAL"}:
            prompt = safe_json_dumps(
                {
                    "field": field,
                    "past_corrections": corrections,
                    "task": "Map this Tableau field to a Power BI friendly name and semantic type. Return pbi_name, semantic_type, tier, confidence, note.",
                }
            )
            result = run_openai_json(
                prompt,
                "You map Tableau fields to Power BI. Return strict JSON with pbi_name, semantic_type, tier, confidence, note.",
            )
            if result:
                mapping["pbi_name"] = to_snake_case(result.get("pbi_name", mapping["pbi_name"]))
                mapping["semantic_type"] = result.get("semantic_type", mapping["semantic_type"])
                mapping["tier"] = result.get("tier", mapping["tier"])
                mapping["confidence"] = int(result.get("confidence", mapping["confidence"]))
                mapping["note"] = result.get("note", "")
        fields.append(mapping)
    summary = summarize_counts(fields, "tier")
    avg_conf = round(sum(item["confidence"] for item in fields) / len(fields), 1) if fields else 0
    result = {
        "fields": fields,
        "summary": {
            "auto": summary.get("AUTO", 0),
            "good": summary.get("GOOD", 0),
            "review": summary.get("REVIEW", 0),
            "manual": summary.get("MANUAL", 0),
            "blocked": summary.get("BLOCKED", 0),
            "confidence_avg": avg_conf,
        },
    }
    session.stage_data[1] = result
    session.stage = 1
    persist_session(session)
    return result


def compute_stage2(session: SessionState) -> dict:
    stage0 = session.stage_data.get(0) or {}
    stage1 = session.stage_data.get(1) or {}
    columns = [field.get("pbi_name") or to_snake_case(field.get("field_name", "")) for field in stage1.get("fields", [])]
    formulas = []
    for field in stage1.get("fields", []):
        if not field.get("formula"):
            continue
        examples = lookup_corrections("formula_translation", field_path=field.get("field_name", ""), limit=5)
        translation = translate_formula_safe(field["formula"], field.get("source_table", "Data"), columns)
        dax = translation.get("dax", "")
        confidence = int(translation.get("confidence", 0))
        tier = translation.get("tier", "REVIEW")
        method = translation.get("method", "unknown")
        brains = ["GPT-4o"] if confidence else []
        if tier != "AUTO" and get_openai_client():
            ai_result = run_openai_json(
                safe_json_dumps(
                    {
                        "field": field,
                        "translation": translation,
                        "examples": examples,
                        "task": "Produce a Power BI DAX translation. Return dax, confidence, notes, tier.",
                    }
                ),
                "You are a senior DAX translator. Return strict JSON only.",
            )
            if ai_result:
                dax = ai_result.get("dax", dax)
                confidence = int(ai_result.get("confidence", confidence or 65))
                tier = ai_result.get("tier", "REVIEW")
                method = "openai_refined"
                brains = ["GPT-4o"]
        if tier == "BLOCKED" and os.getenv("ANTHROPIC_API_KEY"):
            brains.append("Claude")
        if tier == "BLOCKED" and os.getenv("GOOGLE_API_KEY"):
            brains.append("Gemini")
        formulas.append(
            {
                "field_name": field["field_name"],
                "tableau_original": field.get("formula", ""),
                "dax": dax,
                "confidence": confidence,
                "tier": tier,
                "method": method,
                "notes": "Manual review required" if tier == "BLOCKED" else "",
                "brains": brains,
            }
        )
    avg_conf = round(sum(item["confidence"] for item in formulas) / len(formulas), 1) if formulas else 0
    result = {
        "formulas": formulas,
        "summary": {
            "formula_count": len(formulas),
            "blocked": sum(1 for item in formulas if item["tier"] == "BLOCKED"),
            "confidence_avg": avg_conf,
        },
    }
    session.stage_data[2] = result
    session.stage = 2
    persist_session(session)
    return result


VISUAL_MAP = {
    "bar": "clusteredBarChart",
    "line": "lineChart",
    "pie": "pieChart",
    "area": "areaChart",
    "circle": "scatterChart",
    "text": "tableEx",
    "map": "filledMap",
    "automatic": "clusteredColumnChart",
    "ban": "card",
    "square": "treemap",
}


def normalize_field_lookup(stage1_fields: List[dict]) -> Dict[str, dict]:
    lookup: Dict[str, dict] = {}
    for item in stage1_fields:
        names = {
            item.get("field_name", ""),
            to_snake_case(item.get("field_name", "")),
            item.get("pbi_name", ""),
            titleize(item.get("field_name", "")),
        }
        for name in names:
            if name:
                lookup[name.lower()] = item
    return lookup


def find_mapping(lookup: Dict[str, dict], name: str) -> Optional[dict]:
    if not name:
        return None
    return lookup.get(name.lower()) or lookup.get(to_snake_case(name).lower()) or lookup.get(titleize(name).lower())


def build_query_state(bindings: List[dict]) -> dict:
    projections = []
    for binding in bindings:
        field = binding["field"]
        projections.append(
            {
                "bucket": binding["bucket"],
                "queryRef": f"{field['table_name']}[{field['column_name']}]",
                "entity": field["table_name"],
                "property": field["column_name"],
                "semanticType": field["semantic_type"],
            }
        )
    return {"projections": projections}


def bucket_for_field(field: dict) -> str:
    if field["semantic_type"] == "measure":
        return "Values"
    if field["semantic_type"] == "date":
        return "Axis"
    return "Category"


def compute_stage3(session: SessionState) -> dict:
    stage0 = session.stage_data.get(0) or {}
    stage1 = session.stage_data.get(1) or {}
    lookup = normalize_field_lookup(stage1.get("fields", []))
    sheets = []
    for idx, sheet in enumerate(stage0.get("sheets", [])):
        mark_type = (sheet.get("mark_type") or "automatic").lower()
        visual_type = VISUAL_MAP.get(mark_type, "clusteredColumnChart")
        candidate_names: List[str] = []
        candidate_names.extend(sheet.get("dimensions", []))
        candidate_names.extend(sheet.get("measures", []))
        candidate_names.extend(sheet.get("rows_fields", []))
        candidate_names.extend(sheet.get("cols_fields", []))
        bindings: List[dict] = []
        used = set()
        for name in candidate_names:
            mapping = find_mapping(lookup, name)
            if not mapping or mapping.get("ignored"):
                continue
            ref = f"{mapping.get('source_table', 'Data')}[{mapping['pbi_name']}]"
            if ref in used:
                continue
            used.add(ref)
            field_payload = {
                "display_name": mapping["field_name"],
                "column_name": mapping["pbi_name"],
                "table_name": titleize(mapping.get("source_table", "Data")),
                "semantic_type": mapping["semantic_type"],
            }
            bindings.append({"bucket": bucket_for_field(mapping), "field": field_payload})
        if not bindings:
            dimensions = [field for field in stage1.get("fields", []) if field.get("semantic_type") in {"dimension", "date"} and not field.get("ignored")]
            measures = [field for field in stage1.get("fields", []) if field.get("semantic_type") == "measure" and not field.get("ignored")]
            if dimensions:
                field = dimensions[0]
                bindings.append(
                    {
                        "bucket": "Category",
                        "field": {
                            "display_name": field["field_name"],
                            "column_name": field["pbi_name"],
                            "table_name": titleize(field.get("source_table", "Data")),
                            "semantic_type": field["semantic_type"],
                        },
                    }
                )
            if measures:
                field = measures[0]
                bindings.append(
                    {
                        "bucket": "Values",
                        "field": {
                            "display_name": field["field_name"],
                            "column_name": field["pbi_name"],
                            "table_name": titleize(field.get("source_table", "Data")),
                            "semantic_type": field["semantic_type"],
                        },
                    }
                )
        sheets.append(
            {
                "sheet_name": sheet.get("name", f"Sheet {idx+1}"),
                "tableau_mark_type": mark_type,
                "pbi_visual_type": visual_type,
                "bindings": bindings,
                "query_state": build_query_state(bindings),
                "filters": sheet.get("filters", []),
                "position": {"x": 20 + (idx % 2) * 420, "y": 20 + (idx // 2) * 320, "w": 380, "h": 260},
            }
        )
    result = {"sheets": sheets, "summary": {"sheet_count": len(sheets), "filled_maps": sum(1 for s in sheets if s["pbi_visual_type"] == "filledMap")}}
    session.stage_data[3] = result
    session.stage = 3
    persist_session(session)
    return result


def infer_table_role(columns: List[dict]) -> str:
    measures = sum(1 for column in columns if column.get("semantic_type") == "measure")
    dims = sum(1 for column in columns if column.get("semantic_type") in {"dimension", "date", "calculated"})
    return "fact" if measures >= dims else "dimension"


def compute_stage4(session: SessionState) -> dict:
    stage0 = session.stage_data.get(0) or {}
    stage1 = session.stage_data.get(1) or {}
    tables: Dict[str, List[dict]] = {}
    for field in stage1.get("fields", []):
        table_name = titleize(field.get("source_table", "Data"))
        tables.setdefault(table_name, []).append(
            {
                "name": field["pbi_name"],
                "source_name": field["field_name"],
                "semantic_type": field["semantic_type"],
                "data_type": field.get("data_type", "string"),
            }
        )
    table_items = []
    for table_name, columns in tables.items():
        table_items.append({"name": table_name, "role": infer_table_role(columns), "columns": columns})

    relationships = []
    for rel in stage0.get("raw_spec", {}).get("relationships", []):
        left_ref = rel.get("left_ref", "").strip("[]")
        right_ref = rel.get("right_ref", "").strip("[]")
        left_parts = left_ref.split(".")
        right_parts = right_ref.split(".")
        from_table = titleize(left_parts[0]) if left_parts else "Data"
        from_col = to_snake_case(left_parts[-1]) if left_parts else "id"
        to_table = titleize(right_parts[0]) if right_parts else "Lookup"
        to_col = to_snake_case(right_parts[-1]) if right_parts else "id"
        relationships.append(
            {
                "from_table": from_table,
                "from_col": from_col,
                "to_table": to_table,
                "to_col": to_col,
                "cardinality": "1:M",
                "join_type": rel.get("join_type", "inner"),
            }
        )
    if not relationships and len(table_items) >= 2:
        first_fact = table_items[0]["name"]
        for table in table_items[1:]:
            relationships.append(
                {
                    "from_table": first_fact,
                    "from_col": "id",
                    "to_table": table["name"],
                    "to_col": "id",
                    "cardinality": "1:M",
                    "join_type": "inner",
                }
            )

    suggested_measures = []
    for field in stage1.get("fields", []):
        if field.get("semantic_type") == "measure":
            measure_name = f"total_{field['pbi_name']}"
            suggested_measures.append({"name": measure_name, "dax": f"SUM({titleize(field.get('source_table', 'Data'))}[{field['pbi_name']}])"})
    result = {"tables": table_items, "relationships": relationships, "suggested_measures": suggested_measures}
    session.stage_data[4] = result
    session.stage = 4
    persist_session(session)
    return result


def dataframe_to_records(df: Any, limit: int = 5) -> List[dict]:
    if pd is None or df is None:
        return []
    return json.loads(df.head(limit).to_json(orient="records", date_format="iso"))


def dataframe_to_schema(df: Any) -> List[dict]:
    if pd is None or df is None:
        return []
    records = []
    for col in df.columns:
        sample = df[col].dropna().astype(str).head(3).tolist()
        records.append({"name": to_snake_case(col), "source_name": col, "type": str(df[col].dtype), "samples": sample})
    return records


def dataframe_to_json_path(session_id: str, df: Any) -> str:
    path = Path(f"/tmp/drdata_{session_id}_stage5.json")
    if pd is not None and df is not None:
        path.write_text(df.to_json(orient="records", date_format="iso"), encoding="utf-8")
    return str(path)


def build_tmdl_tables(schema: List[dict], table_name: str = "Data") -> List[dict]:
    tables = [
        {
            "name": titleize(table_name),
            "columns": [
                {
                    "name": item["name"],
                    "source_name": item.get("source_name", item["name"]),
                    "data_type": item["type"],
                }
                for item in schema
            ],
        }
    ]
    return tables


def compute_stage5(session: SessionState) -> dict:
    raw_spec = session.stage_data.get(0, {}).get("raw_spec", {})
    num_rows = int(session.corrections.get(5, {}).get("num_rows", 2000))
    output_dir = ensure_dir(f"/tmp/drdata_{session.session_id}_synthetic")
    df, csv_path, schema = generate_from_tableau_spec(raw_spec, num_rows=num_rows, output_dir=str(output_dir))
    schema_rows = []
    for item in schema:
        source_name = item.get("name", "column")
        if pd is not None and source_name in df.columns:
            samples = df[source_name].dropna().astype(str).head(3).tolist()
            dtype = str(df[source_name].dtype)
        else:
            samples = []
            dtype = item.get("datatype", "string")
        schema_rows.append({"name": to_snake_case(source_name), "source_name": source_name, "type": dtype, "samples": samples})
    preview_rows = dataframe_to_records(df, limit=5)
    result = {
        "num_rows": num_rows,
        "schema": schema_rows,
        "preview_rows": preview_rows,
        "csv_path": csv_path,
        "tmdl_tables": build_tmdl_tables(schema_rows),
    }
    session.stage_data[5] = result
    session.dataframes[5] = dataframe_to_json_path(session.session_id, df)
    session.stage = 5
    persist_session(session)
    return result


def tmdl_data_type(dtype: str) -> str:
    lowered = (dtype or "").lower()
    if "int" in lowered:
        return "int64"
    if any(token in lowered for token in ["float", "double", "real", "decimal"]):
        return "double"
    if "bool" in lowered:
        return "bool"
    if "date" in lowered or "time" in lowered:
        return "datetime"
    return "string"


def make_visual_payload(sheet: dict, index: int) -> dict:
    return {
        "name": f"Visual_{index+1}",
        "type": sheet["pbi_visual_type"],
        "title": sheet["sheet_name"],
        "position": sheet.get("position", {"x": 0, "y": 0, "w": 300, "h": 200}),
        "config": {"singleVisual": {"visualType": sheet["pbi_visual_type"], "projections": sheet["query_state"]["projections"]}},
        "queryState": sheet["query_state"],
        "bindings": sheet["bindings"],
        "filters": sheet.get("filters", []),
    }


def build_tmdl_table_text(table: dict, measures: List[dict]) -> str:
    lines = [f"table {table['name']}", "{"]
    for column in table.get("columns", []):
        lines.extend(
            [
                f"    column {column['name']}",
                "    {",
                f"        dataType: {tmdl_data_type(column.get('data_type', 'string'))}",
                f"        sourceColumn: \"{column.get('source_name', column['name'])}\"",
                "    }",
            ]
        )
    for measure in measures:
        lines.extend(
            [
                f"    measure {measure['name']}",
                "    {",
                f"        expression: \"{measure['dax'].replace(chr(34), chr(39))}\"",
                "    }",
            ]
        )
    lines.append("}")
    return "\n".join(lines) + "\n"


def build_relationships_tmdl(relationships: List[dict]) -> str:
    lines = ["relationships"]
    lines.append("{")
    for rel in relationships:
        lines.extend(
            [
                "    relationship",
                "    {",
                f"        fromColumn: {rel['from_table']}[{rel['from_col']}]",
                f"        toColumn: {rel['to_table']}[{rel['to_col']}]",
                f"        cardinality: {rel['cardinality']}",
                "    }",
            ]
        )
    lines.append("}")
    return "\n".join(lines) + "\n"


def read_stage5_records(session: SessionState) -> List[dict]:
    json_path = session.dataframes.get(5, "")
    if json_path and Path(json_path).exists():
        return json.loads(Path(json_path).read_text(encoding="utf-8"))
    return []


def write_pbip_project(session: SessionState) -> Tuple[str, List[dict], List[str]]:
    stage3 = session.stage_data.get(3) or {}
    stage4 = session.stage_data.get(4) or {}
    stage5 = session.stage_data.get(5) or {}
    stage2 = session.stage_data.get(2) or {}

    project_root = Path(f"/tmp/drdata_{session.session_id}_pbip")
    if project_root.exists():
        shutil.rmtree(project_root)
    project_root.mkdir(parents=True, exist_ok=True)

    display_name = titleize(Path(session.workbook_name or "DrData").stem)
    report_dir = project_root / f"{display_name}.Report"
    semantic_dir = project_root / f"{display_name}.SemanticModel"
    report_def = ensure_dir(report_dir / "definition")
    pages_dir = ensure_dir(report_def / "pages")
    semantic_def = ensure_dir(semantic_dir / "definition")
    semantic_tables_dir = ensure_dir(semantic_def / "tables")

    manifest = {
        "version": "1.0",
        "artifacts": [{"type": "report", "path": f"{display_name}.Report"}],
    }
    (project_root / f"{display_name}.pbip").write_text(safe_json_dumps(manifest), encoding="utf-8")
    (report_dir / "definition.pbir").write_text(
        safe_json_dumps(
            {
                "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definitionProperties/2.0.0/schema.json",
                "version": "4.0",
                "datasetReference": {"byPath": f"../{display_name}.SemanticModel"},
            }
        ),
        encoding="utf-8",
    )
    report_json = {"name": display_name, "version": "1.0", "themeCollection": {"baseTheme": "CY24SU08"}}
    (report_def / "report.json").write_text(safe_json_dumps(report_json), encoding="utf-8")

    page_refs = []
    for index, sheet in enumerate(stage3.get("sheets", [])):
        page_name = f"Page_{index+1}"
        page_dir = ensure_dir(pages_dir / page_name)
        visual_payload = make_visual_payload(sheet, index)
        page_refs.append({"name": page_name, "displayName": sheet["sheet_name"], "ordinal": index})
        (page_dir / "page.json").write_text(
            safe_json_dumps(
                {
                    "name": page_name,
                    "displayName": sheet["sheet_name"],
                    "size": {"width": 1280, "height": 720},
                    "visualContainers": [{"name": visual_payload["name"], "type": visual_payload["type"]}],
                }
            ),
            encoding="utf-8",
        )
        (page_dir / "visual.json").write_text(safe_json_dumps(visual_payload), encoding="utf-8")
    (pages_dir / "pages.json").write_text(safe_json_dumps({"pages": page_refs}), encoding="utf-8")

    records = read_stage5_records(session)
    inline_table = {
        "name": "Data",
        "mode": "import",
        "partitions": [{"name": "Data", "mode": "import", "source": {"type": "inline", "rows": records[:2000]}}],
    }
    (semantic_def / "database.tmdl").write_text(
        "\n".join(
            [
                f"model {display_name}",
                "{",
                "    culture: en-US",
                "    defaultPowerBIDataSourceVersion: powerBI_V3",
                "}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    table_measures_by_table: Dict[str, List[dict]] = {}
    for measure in stage2.get("formulas", []):
        table_measures_by_table.setdefault("Data", []).append({"name": to_snake_case(measure["field_name"]), "dax": measure["dax"]})
    tables = stage4.get("tables") or [{"name": "Data", "columns": stage5.get("schema", [])}]
    for table in tables:
        table_payload = {"name": table["name"], "columns": table.get("columns", stage5.get("schema", []))}
        text = build_tmdl_table_text(table_payload, table_measures_by_table.get(table["name"], []))
        (semantic_tables_dir / f"{table['name']}.tmdl").write_text(text, encoding="utf-8")
    (semantic_tables_dir / "DataInline.json").write_text(safe_json_dumps(inline_table), encoding="utf-8")
    (semantic_def / "relationships.tmdl").write_text(build_relationships_tmdl(stage4.get("relationships", [])), encoding="utf-8")

    files = []
    for file_path in sorted(project_root.rglob("*")):
        if not file_path.is_file():
            continue
        rel = str(file_path.relative_to(project_root))
        content = file_path.read_text(encoding="utf-8", errors="ignore")
        files.append({"path": rel, "content": content, "size": file_path.stat().st_size})

    warnings = []
    if not stage3.get("sheets"):
        warnings.append("No visual mappings were generated.")
    if not stage5.get("schema"):
        warnings.append("Synthetic data schema is empty.")
    if any("Category" in item["content"] and "queryState" in item["content"] for item in files):
        warnings.append("Found generic Category references; review bindings before publish.")
    return str(project_root), files, warnings


def compute_stage6(session: SessionState) -> dict:
    pbip_path, files, warnings = write_pbip_project(session)
    result = {
        "pbip_path": pbip_path,
        "display_name": titleize(Path(session.workbook_name or "DrData").stem),
        "files": files,
        "warnings": warnings,
        "manifest_review": {"file_count": len(files), "warning_count": len(warnings)},
    }
    session.stage_data[6] = result
    session.stage = 6
    persist_session(session)
    return result


def compute_stage7(session: SessionState) -> dict:
    stage6 = session.stage_data.get(6) or {}
    pbip_path = stage6.get("pbip_path")
    if not pbip_path:
        raise HTTPException(status_code=400, detail="PBIP build not available")
    qa = QAAgent(source_spec=session.stage_data.get(0, {}).get("raw_spec"))
    result = qa.run_full_qa(pbip_path)
    issues = result.get("issues", [])
    fixes = result.get("fixes", [])
    fidelity_score = max(0, min(100, 100 - (len(issues) * 12) + (len(fixes) * 4)))
    payload = {
        "qa": result,
        "checks": [{"name": item, "passed": False} for item in issues] or [{"name": "Deterministic QA", "passed": True}],
        "auto_fixes_applied": fixes,
        "remaining_issues": issues,
        "fidelity_score": fidelity_score,
        "can_publish": bool(result.get("passed")),
    }
    session.stage_data[7] = payload
    session.stage = 7
    persist_session(session)
    return payload


def render_audit_html(summary: dict) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Dr. Data Audit</title><style>body{{font-family:Arial,sans-serif;padding:24px}}pre{{background:#f4f4f4;padding:16px;white-space:pre-wrap}}</style></head>
<body><h1>Dr. Data Audit Summary</h1><pre>{json.dumps(summary, indent=2)}</pre></body></html>"""


def create_output_zip(session: SessionState, audit_summary: dict) -> Tuple[str, str]:
    stage6 = session.stage_data.get(6) or {}
    pbip_path = Path(stage6.get("pbip_path", ""))
    zip_path = Path(f"/tmp/drdata_{session.session_id}_output.zip")
    formulas = session.stage_data.get(2, {}).get("formulas", [])
    limitations = {
        "blocked_formulas": [item["field_name"] for item in formulas if item.get("tier") == "BLOCKED"],
        "warnings": stage6.get("warnings", []),
    }
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        if pbip_path.exists():
            for file_path in pbip_path.rglob("*"):
                if file_path.is_file():
                    zf.write(file_path, arcname=f"pbip/{file_path.relative_to(pbip_path)}")
        zf.writestr("audit/audit_summary.json", safe_json_dumps(audit_summary))
        zf.writestr("audit/audit_summary.html", render_audit_html(audit_summary))
        zf.writestr("audit/formulas.dax", "\n\n".join(f"// {item['field_name']}\n{item['dax']}" for item in formulas))
        zf.writestr("audit/limitations.json", safe_json_dumps(limitations))
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    return str(zip_path), digest


def compute_stage8(session: SessionState) -> dict:
    stage6 = session.stage_data.get(6) or {}
    pbip_path = stage6.get("pbip_path")
    if not pbip_path:
        raise HTTPException(status_code=400, detail="PBIP build not available")
    display_name = stage6.get("display_name") or titleize(Path(session.workbook_name or "DrData").stem)
    workspace_id = WORKSPACE_ID
    progress = [
        {"step": "Publish", "status": "running"},
        {"step": "Index", "status": "pending"},
        {"step": "QA Loop", "status": "pending"},
        {"step": "Audit", "status": "pending"},
        {"step": "Package", "status": "pending"},
    ]

    token = get_access_token(FABRIC_SCOPE)
    publish_result = publish_pbip(token, workspace_id, pbip_path, display_name)
    if publish_result.get("error"):
        raise HTTPException(status_code=502, detail=safe_json_dumps(publish_result))
    progress[0]["status"] = "done"
    progress[1]["status"] = "running"
    time.sleep(15)
    pages = get_report_pages(token, workspace_id, publish_result["report_id"])
    progress[1]["status"] = "done"
    progress[2]["status"] = "running"
    qa_agent = QAAgent(
        source_spec=session.stage_data.get(0, {}).get("raw_spec"),
        config={"field_mappings": session.stage_data.get(1, {}), "pbip_path": pbip_path},
    )
    qa_result: dict = {"status": "skipped"}
    try:
        if hasattr(qa_agent, "run_post_publish_qa"):
            qa_result = qa_agent.run_post_publish_qa(
                token,
                workspace_id,
                publish_result["report_id"],
                publish_result.get("semantic_model_id", ""),
                pbip_path,
            )
        else:
            qa_result = {"status": "not_supported"}
    except Exception as exc:
        qa_result = {"status": "error", "detail": str(exc)}
    progress[2]["status"] = "done"
    progress[3]["status"] = "running"

    audit_engine = AuditEngine()
    audit_summary = {
        "field_count": session.stage_data.get(0, {}).get("summary", {}).get("field_count", 0),
        "confidence_avg": session.stage_data.get(1, {}).get("summary", {}).get("confidence_avg", 0),
        "corrections_applied": sum(len(value) for value in session.corrections.values()),
        "fidelity_score": session.stage_data.get(7, {}).get("fidelity_score", 0),
        "report_pages": len(pages),
        "qa_result": qa_result,
    }
    if pd is not None:
        json_path = session.dataframes.get(5)
        if json_path and Path(json_path).exists():
            df = pd.read_json(json_path)
            report = audit_engine.audit_dataframe(df, source_name=session.workbook_name or "synthetic data")
            audit_summary["audit_findings"] = len(getattr(report, "findings", []))
    progress[3]["status"] = "done"
    progress[4]["status"] = "running"
    zip_path, digest = create_output_zip(session, audit_summary)
    progress[4]["status"] = "done"

    session.pbi_report_id = publish_result.get("report_id", "")
    session.pbi_workspace_id = workspace_id
    session.zip_path = zip_path
    session.status = "complete"
    session.stage = 8
    report_url = publish_result.get("report_url") or f"https://app.powerbi.com/groups/{workspace_id}/reports/{session.pbi_report_id}"

    payload = {
        "progress": progress,
        "report_url": report_url,
        "zip_download_url": f"/api/download/{session.session_id}",
        "report_id": session.pbi_report_id,
        "qa_result": qa_result,
        "audit_summary": audit_summary,
        "pages": pages,
        "zip_sha256": digest,
    }
    session.stage_data[8] = payload
    persist_session(session)
    return payload


COMPUTE_STAGE = {
    0: compute_stage0,
    1: compute_stage1,
    2: compute_stage2,
    3: compute_stage3,
    4: compute_stage4,
    5: compute_stage5,
    6: compute_stage6,
    7: compute_stage7,
    8: compute_stage8,
}


def stage_payload(session: SessionState, stage_num: int) -> dict:
    if stage_num < 0 or stage_num > 8:
        raise HTTPException(status_code=404, detail="Invalid stage")
    data = session.stage_data.get(stage_num, {})
    status = "approved" if stage_num in session.approved else ("active" if session.stage == stage_num else "pending")
    if session.status == "error":
        status = "error"
    return {
        "stage": stage_num,
        "name": STAGE_NAMES[stage_num],
        "status": status,
        "data": data,
        "editable_fields": EDITABLE_FIELDS.get(stage_num, []),
        "is_approved": stage_num in session.approved,
        "can_proceed": bool(data),
    }


def current_stage_summaries(session: SessionState) -> dict:
    summaries = {}
    for stage_num, data in session.stage_data.items():
        if stage_num == 0:
            summaries[stage_num] = data.get("summary", {})
        elif stage_num == 1:
            summaries[stage_num] = data.get("summary", {})
        elif stage_num == 2:
            summaries[stage_num] = data.get("summary", {})
        elif stage_num == 7:
            summaries[stage_num] = {"issues_count": len(data.get("remaining_issues", [])), "fidelity_score": data.get("fidelity_score", 0)}
        else:
            summaries[stage_num] = {"keys": list(data.keys())[:5]}
    return summaries


def build_pipeline_context(session: SessionState) -> dict:
    return {
        "session_id": session.session_id,
        "workbook_name": session.workbook_name,
        "current_stage": session.stage,
        "current_stage_name": STAGE_NAMES[session.stage] if 0 <= session.stage < len(STAGE_NAMES) else "NOT_STARTED",
        "approved_stages": session.approved,
        "status": session.status,
        "stage_summaries": current_stage_summaries(session),
    }


ACTION_RE = re.compile(r"<ACTION>(.*?)</ACTION>", re.DOTALL)


def execute_chat_action(session: SessionState, action: dict) -> Tuple[dict, Optional[dict]]:
    action_name = action.get("action")
    if action_name == "goto_stage":
        stage = int(action.get("stage", session.stage))
        if stage not in session.stage_data:
            raise HTTPException(status_code=400, detail=f"Stage {stage} is not available")
        session.stage = stage
        persist_session(session)
        return {"action": "goto_stage", "stage": stage}, session.stage_data.get(stage)
    if action_name == "redo_field":
        field_name = action.get("field", "")
        if not field_name:
            raise HTTPException(status_code=400, detail="redo_field requires field")
        stage2 = session.stage_data.get(2, {})
        for formula in stage2.get("formulas", []):
            if formula["field_name"].lower() == field_name.lower():
                translated = translate_formula_safe(formula["tableau_original"], "Data", [])
                formula["dax"] = translated.get("dax", formula["dax"])
                formula["confidence"] = translated.get("confidence", formula["confidence"])
                formula["method"] = translated.get("method", formula["method"])
                persist_session(session)
                return {"action": "redo_field", "field": field_name}, stage2
        raise HTTPException(status_code=404, detail=f"Field not found: {field_name}")
    if action_name == "undo":
        stage = int(action.get("stage", session.stage))
        restored = undo_stage_internal(session, stage)
        return {"action": "undo", "stage": stage}, restored
    if action_name == "approve_stage":
        stage = int(action.get("stage", session.stage))
        response = approve_stage_internal(session, stage)
        return {"action": "approve_stage", "stage": stage}, response
    if action_name == "skip_sheet":
        sheet_name = action.get("sheet", "")
        stage3 = session.stage_data.get(3, {})
        original_len = len(stage3.get("sheets", []))
        stage3["sheets"] = [sheet for sheet in stage3.get("sheets", []) if sheet.get("sheet_name") != sheet_name]
        if len(stage3["sheets"]) == original_len:
            raise HTTPException(status_code=404, detail=f"Sheet not found: {sheet_name}")
        persist_session(session)
        return {"action": "skip_sheet", "sheet": sheet_name}, stage3
    if action_name == "rerun_formula":
        return execute_chat_action(session, {"action": "redo_field", "field": action.get("field", "")})
    raise HTTPException(status_code=400, detail=f"Unsupported action: {action_name}")


def build_chat_prompt(session: SessionState, message: str) -> Tuple[str, str]:
    pipeline_context_json = safe_json_dumps(build_pipeline_context(session))
    correction_stats_json = safe_json_dumps(get_correction_stats())
    system_prompt = f"""
You are Dr. Data, a PhD-level AI expert in Tableau and Power BI migration.
You are embedded in an analyst's migration pipeline. You have full context of
every stage of their current migration. You are precise, direct, and confident.
You never hedge. You know DAX, Tableau calculations, TMDL, PBIP schema, and
Power BI best practices cold.

You can answer questions AND execute pipeline commands. When the analyst asks
you to do something actionable, respond with a JSON command block at the end
of your message, formatted exactly as:
<ACTION>{{"action": "...", ...params}}</ACTION>

Available actions:
- goto_stage: {{"action": "goto_stage", "stage": N}}
- redo_field: {{"action": "redo_field", "field": "field_name"}}
- undo: {{"action": "undo", "stage": N}}
- approve_stage: {{"action": "approve_stage", "stage": N}}
- skip_sheet: {{"action": "skip_sheet", "sheet": "sheet_name"}}
- rerun_formula: {{"action": "rerun_formula", "field": "field_name", "hint": "..."}}

Current pipeline context:
{pipeline_context_json}

Correction learning stats:
{correction_stats_json}
""".strip()
    user_prompt = message
    return system_prompt, user_prompt


def approve_stage_internal(session: SessionState, stage_num: int) -> dict:
    if stage_num not in session.stage_data:
        raise HTTPException(status_code=400, detail=f"Stage {stage_num} has not been computed")
    snapshot_stage(session, stage_num, label="approve")
    if stage_num not in session.approved:
        session.approved.append(stage_num)
    if stage_num == 8:
        persist_session(session)
        return {"ok": True, "next_stage": 8, "next_stage_data": session.stage_data.get(8)}
    next_stage = stage_num + 1
    try:
        next_stage_data = COMPUTE_STAGE[next_stage](session)
        session.stage = next_stage
        persist_session(session)
        return {"ok": True, "next_stage": next_stage, "next_stage_data": next_stage_data}
    except HTTPException:
        session.status = "error"
        persist_session(session)
        raise
    except Exception as exc:
        session.status = "error"
        persist_session(session)
        raise HTTPException(status_code=500, detail=str(exc))


def undo_stage_internal(session: SessionState, stage_num: int) -> dict:
    for entry in reversed(session.stage_history):
        if entry.get("stage") == stage_num:
            session.stage_data[stage_num] = deep_copy_jsonable(entry["data"])
            session.approved = [stage for stage in session.approved if stage < stage_num]
            for later_stage in range(stage_num + 1, 9):
                session.stage_data.pop(later_stage, None)
            session.stage = stage_num
            session.status = "active"
            persist_session(session)
            return {"ok": True, "restored_data": session.stage_data[stage_num]}
    raise HTTPException(status_code=404, detail=f"No undo snapshot available for stage {stage_num}")


@app.post("/api/session/start")
def start_session() -> dict:
    session_id = str(uuid.uuid4())
    workbook_name = ""
    session = SessionState(
        session_id=session_id,
        created_at=utcnow_iso(),
        twbx_path="",
        workbook_name=workbook_name,
        stage=0,
        stage_history=[],
        stage_data={},
        corrections={},
        approved=[],
        chat_history=[],
        pbi_report_id="",
        pbi_workspace_id=WORKSPACE_ID,
        zip_path="",
        status="active",
    )
    session.pipeline_state = PipelineState(session_id, workbook_name=workbook_name).to_dict()
    _sessions[session_id] = session
    persist_session(session)
    return {"session_id": session_id}


@app.post("/api/upload/{session_id}")
async def upload_workbook(session_id: str, file: UploadFile = File(...)) -> dict:
    session = get_session_or_404(session_id)
    filename = file.filename or "workbook.twbx"
    if not filename.lower().endswith((".twb", ".twbx")):
        raise HTTPException(status_code=400, detail="Only .twb and .twbx files are supported")
    upload_dir = ensure_dir(Path("/tmp/drdata_uploads") / session_id)
    target = upload_dir / filename
    contents = await file.read()
    target.write_bytes(contents)
    session.twbx_path = str(target)
    session.workbook_name = filename
    compute_stage0(session)
    return {"file_id": target.name, "filename": filename, "size": len(contents)}


@app.get("/api/stage/{session_id}/{stage_num}")
def get_stage(session_id: str, stage_num: int) -> dict:
    session = get_session_or_404(session_id)
    return stage_payload(session, stage_num)


@app.post("/api/stage/{session_id}/{stage_num}/edit")
def edit_stage(session_id: str, stage_num: int, payload: EditRequest) -> dict:
    session = get_session_or_404(session_id)
    stage_data = session.stage_data.get(stage_num)
    if stage_data is None:
        raise HTTPException(status_code=404, detail=f"Stage {stage_num} data not found")
    snapshot_stage(session, stage_num, label="edit")
    try:
        set_path_value(stage_data, payload.field_path, payload.corrected_value)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid field_path {payload.field_path}: {exc}")
    session.corrections.setdefault(stage_num, {})[payload.field_path] = {
        "original_value": payload.original_value,
        "corrected_value": payload.corrected_value,
        "timestamp": utcnow_iso(),
    }
    store_correction(
        session.session_id,
        STAGE_NAMES[stage_num].lower(),
        payload.field_path,
        payload.original_value,
        payload.corrected_value,
        STAGE_NAMES[stage_num].lower(),
        worksheet_name=session.workbook_name,
    )
    persist_session(session)
    return {"ok": True, "correction_id": f"{session.session_id}:{stage_num}:{slugify(payload.field_path)}"}


@app.post("/api/stage/{session_id}/{stage_num}/approve")
def approve_stage(session_id: str, stage_num: int) -> dict:
    session = get_session_or_404(session_id)
    return approve_stage_internal(session, stage_num)


@app.post("/api/stage/{session_id}/{stage_num}/undo")
def undo_stage(session_id: str, stage_num: int) -> dict:
    session = get_session_or_404(session_id)
    return undo_stage_internal(session, stage_num)


@app.post("/api/chat/{session_id}")
def chat(session_id: str, payload: ChatRequest) -> dict:
    session = get_session_or_404(session_id)
    session.chat_history.append({"role": "user", "content": payload.message})
    system_prompt, user_prompt = build_chat_prompt(session, payload.message)
    try:
        response_text = run_openai_text(system_prompt, user_prompt)
    except Exception as exc:
        response_text = f"Dr. Data is offline: {exc}"
    action_taken = None
    stage_data = None
    action_match = ACTION_RE.search(response_text)
    if action_match:
        try:
            action_dict = json.loads(action_match.group(1))
            action_taken, stage_data = execute_chat_action(session, action_dict)
        except Exception as exc:
            action_taken = {"action": "error", "detail": str(exc)}
        response_text = ACTION_RE.sub("", response_text).strip()
    session.chat_history.append({"role": "assistant", "content": response_text})
    persist_session(session)
    return {"message": response_text, "action_taken": action_taken, "stage_data": stage_data}


@app.get("/api/download/{session_id}")
def download(session_id: str) -> FileResponse:
    session = get_session_or_404(session_id)
    if not session.zip_path or not Path(session.zip_path).exists():
        raise HTTPException(status_code=404, detail="ZIP package is not available yet")
    return FileResponse(session.zip_path, media_type="application/zip", filename=Path(session.zip_path).name)


@app.get("/api/session/{session_id}")
def get_session_summary(session_id: str) -> dict:
    session = get_session_or_404(session_id)
    return {
        "session_id": session.session_id,
        "created_at": session.created_at,
        "workbook_name": session.workbook_name,
        "stage": session.stage,
        "approved": session.approved,
        "status": session.status,
        "pbi_report_id": session.pbi_report_id,
        "pbi_workspace_id": session.pbi_workspace_id,
        "zip_path": session.zip_path,
        "stage_history": session.stage_history[-10:],
        "stage_data": session.stage_data,
        "corrections": session.corrections,
        "chat_history": session.chat_history[-30:],
    }


@app.get("/api/corrections/stats")
def corrections_stats() -> dict:
    return get_correction_stats()


@app.post("/api/deploy")
def deploy_frontend() -> dict:
    index_path = Path("/home/zamoritacr/taop/drdata-v2/index.html")
    if not index_path.exists():
        raise HTTPException(status_code=404, detail=f"Frontend file not found: {index_path}")
    host = os.getenv("GREENGEEKS_HOST", "")
    username = os.getenv("GREENGEEKS_USERNAME", "")
    password = os.getenv("GREENGEEKS_API_TOKEN", "")
    if not all([host, username, password]):
        raise HTTPException(status_code=500, detail="Missing GreenGeeks FTP credentials")
    payload = index_path.read_bytes()
    timestamp = utcnow_iso()
    try:
        with ftplib.FTP(host, timeout=30) as ftp:
            ftp.login(user=username, passwd=password)
            ftp.cwd("/public_html/drdata")
            ftp.storbinary("STOR index.html", io.BytesIO(payload))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"FTP deploy failed: {exc}")
    return {"ok": True, "bytes_uploaded": len(payload), "timestamp": timestamp}


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": "3.0", "model": OPENAI_MODEL}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8505)
