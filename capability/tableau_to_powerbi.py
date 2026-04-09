"""
Tableau-to-Power-BI capability -- REAL integration with Dr. Data engine.

Accepts a TWB/TWBX file, runs the full parser + formula transpiler + direct mapper,
and produces a migration artifact bundle:
  - tableau_spec.json         (full parsed Tableau metadata)
  - model_mapping.json        (tables, relationships, measures, DAX translations)
  - migration_plan.md         (structured migration plan + risk list)
  - dax_translations.json     (every calculated field -> DAX with confidence scores)
  - pbix_build_instructions.md (step-by-step Power BI rebuild guide)
  - pbip_config.json          (direct mapper output, ready for PBIP generator)
"""

import sys
import os
import json
from pathlib import Path
from typing import Optional

# Add dr-data to path for direct integration
DR_DATA_PATH = os.getenv("DR_DATA_PATH", "/home/zamoritacr/taop-repos/dr-data")
if DR_DATA_PATH not in sys.path:
    sys.path.insert(0, DR_DATA_PATH)


def _parse_twb(file_path: str) -> dict:
    """Parse TWB/TWBX using the real enhanced_tableau_parser."""
    from core.enhanced_tableau_parser import parse_twb
    return parse_twb(file_path)


def _transpile_calculated_fields(calc_fields: list, datasources: list) -> list[dict]:
    """Transpile all calculated fields from Tableau to DAX using the real transpiler."""
    from core.formula_transpiler import TableauFormulaTranspiler

    # Build field resolution map from datasources
    field_map = {}
    for ds in datasources:
        for col in ds.get("columns", []):
            name = col if isinstance(col, str) else col.get("name", "")
            if name:
                field_map[name] = name

    transpiler = TableauFormulaTranspiler(field_resolution_map=field_map, table_name="Data")

    results = []
    for cf in calc_fields:
        name = cf.get("name", "Unknown")
        formula = cf.get("formula", "")
        if not formula:
            results.append({
                "name": name,
                "tableau_formula": "",
                "dax": "",
                "confidence": 0.0,
                "warnings": ["Empty formula"],
                "parse_success": False,
            })
            continue

        tx = transpiler.transpile(formula)
        results.append({
            "name": name,
            "tableau_formula": formula,
            "dax": tx["dax"],
            "confidence": tx["confidence"],
            "warnings": tx["warnings"],
            "parse_success": tx["parse_success"],
        })

    return results


def _build_model_mapping(tableau_spec: dict, dax_translations: list) -> dict:
    """Build a model mapping document: tables, relationships, measures."""
    datasources = tableau_spec.get("datasources", [])
    relationships = tableau_spec.get("relationships", [])

    tables = []
    for ds in datasources:
        table = {
            "source_name": ds.get("name", ""),
            "caption": ds.get("caption", ds.get("name", "")),
            "connection_type": ds.get("connection_type", "unknown"),
            "columns": [],
        }
        for col in ds.get("columns", []):
            if isinstance(col, str):
                table["columns"].append({"name": col, "type": "string"})
            elif isinstance(col, dict):
                table["columns"].append({
                    "name": col.get("name", ""),
                    "type": col.get("datatype", "string"),
                    "role": col.get("role", ""),
                })
        tables.append(table)

    measures = []
    for tx in dax_translations:
        if tx["parse_success"] and tx["dax"]:
            measures.append({
                "name": tx["name"],
                "dax_expression": tx["dax"],
                "confidence": tx["confidence"],
                "warnings": tx["warnings"],
            })

    return {
        "tables": tables,
        "relationships": [
            {
                "from_table": r.get("from_table", ""),
                "from_field": r.get("from_field", ""),
                "to_table": r.get("to_table", ""),
                "to_field": r.get("to_field", ""),
                "type": r.get("type", "inner"),
            }
            for r in relationships
        ],
        "measures": measures,
        "total_calculated_fields": len(dax_translations),
        "successfully_translated": sum(1 for t in dax_translations if t["parse_success"]),
        "needs_manual_review": sum(1 for t in dax_translations if not t["parse_success"]),
    }


def _build_migration_plan(tableau_spec: dict, model_mapping: dict, dax_translations: list) -> str:
    """Generate a structured migration plan with risk assessment."""
    worksheets = tableau_spec.get("worksheets", [])
    dashboards = tableau_spec.get("dashboards", [])
    ds = tableau_spec.get("datasources", [])
    calc = tableau_spec.get("calculated_fields", [])
    params = tableau_spec.get("parameters", [])
    filters = tableau_spec.get("filters", [])

    total_tx = len(dax_translations)
    ok_tx = sum(1 for t in dax_translations if t["parse_success"])
    fail_tx = total_tx - ok_tx
    low_conf = [t for t in dax_translations if t["parse_success"] and t["confidence"] < 0.7]

    risks = []
    if fail_tx > 0:
        risks.append(f"- **HIGH**: {fail_tx} calculated field(s) failed DAX translation -- manual conversion required")
    if low_conf:
        risks.append(f"- **MEDIUM**: {len(low_conf)} translation(s) have low confidence (<70%) -- review DAX output")
    if any(d.get("connection_type") in ("hyper", "tde") for d in ds):
        risks.append("- **MEDIUM**: Hyper/TDE data extract detected -- must re-extract or connect to source")
    if params:
        risks.append(f"- **LOW**: {len(params)} parameter(s) detected -- convert to Power BI What-If parameters")
    if tableau_spec.get("has_hyper"):
        risks.append("- **MEDIUM**: .hyper file detected inside .twbx -- data must be exported separately")
    if not risks:
        risks.append("- No significant risks detected")

    # Chart types in use
    chart_types = set()
    for ws in worksheets:
        ct = ws.get("chart_type", "automatic")
        if ct and ct != "skip":
            chart_types.add(ct)

    plan = f"""# Tableau-to-Power-BI Migration Plan

## Source Workbook Summary

| Metric | Count |
|--------|-------|
| Datasources | {len(ds)} |
| Worksheets | {len(worksheets)} |
| Dashboards | {len(dashboards)} |
| Calculated Fields | {len(calc)} |
| Parameters | {len(params)} |
| Filters | {len(filters)} |
| Chart Types | {', '.join(sorted(chart_types)) or 'none'} |

## DAX Translation Summary

- Total calculated fields: {total_tx}
- Successfully translated: {ok_tx} ({(ok_tx/total_tx*100) if total_tx else 0:.0f}%)
- Failed (manual needed): {fail_tx}
- Low confidence (<70%): {len(low_conf)}

## Risk Assessment

{chr(10).join(risks)}

## Migration Steps

### Phase 1: Data Model Setup
1. Create a new Power BI Desktop file (.pbix)
2. Connect to the original data source(s):
"""

    for i, d in enumerate(ds, 1):
        conn = d.get("connection_type", "unknown")
        name = d.get("caption", d.get("name", f"Source {i}"))
        plan += f"   - **{name}** (original connection: {conn})\n"

    plan += """3. Import or DirectQuery as appropriate
4. Set up table relationships as documented in model_mapping.json

### Phase 2: Measures and Calculated Fields
1. Create DAX measures from dax_translations.json
2. Review and fix any failed/low-confidence translations
3. Create calculated columns where Tableau used row-level calculations

### Phase 3: Visual Reconstruction
"""

    for i, ws in enumerate(worksheets, 1):
        ct = ws.get("chart_type", "automatic")
        name = ws.get("name", f"Sheet {i}")
        plan += f"{i}. **{name}** -- {ct} chart\n"
        rows = ws.get("rows_fields", [])
        cols = ws.get("cols_fields", [])
        if rows:
            plan += f"   - Rows/Y-axis: {', '.join(rows)}\n"
        if cols:
            plan += f"   - Columns/X-axis: {', '.join(cols)}\n"

    plan += """
### Phase 4: Dashboard Assembly
"""

    for i, db in enumerate(dashboards, 1):
        name = db.get("name", f"Dashboard {i}")
        ws_used = db.get("worksheets_used", [])
        plan += f"{i}. **{name}** -- contains: {', '.join(ws_used) if ws_used else 'TBD'}\n"

    plan += """
### Phase 5: Validation
1. Compare visual output side-by-side with Tableau
2. Verify measure calculations match
3. Test all filters and slicers
4. Validate date hierarchies and drill-down behavior
5. Check conditional formatting and tooltips
"""

    return plan


def _build_instructions(tableau_spec: dict, model_mapping: dict) -> str:
    """Generate step-by-step Power BI build instructions."""
    ds = tableau_spec.get("datasources", [])
    tables = model_mapping.get("tables", [])
    measures = model_mapping.get("measures", [])
    rels = model_mapping.get("relationships", [])

    instructions = """# Power BI Build Instructions

## Prerequisites
- Power BI Desktop (latest version)
- Access to original data source(s)
- This artifact bundle (all JSON files)

## Step 1: Create New Report

1. Open Power BI Desktop
2. Save as your project name

## Step 2: Connect Data Sources

"""

    for i, d in enumerate(ds, 1):
        name = d.get("caption", d.get("name", f"Source {i}"))
        conn = d.get("connection_type", "unknown")
        instructions += f"""### Source {i}: {name}
- Original connection type: **{conn}**
- In Power BI: Home > Get Data > choose appropriate connector
- Load all required tables

"""

    instructions += "## Step 3: Set Up Relationships\n\n"
    if rels:
        instructions += "In Model view, create these relationships:\n\n"
        for r in rels:
            instructions += (
                f"- `{r['from_table']}.{r['from_field']}` -> "
                f"`{r['to_table']}.{r['to_field']}` ({r['type']})\n"
            )
    else:
        instructions += "No explicit relationships found -- Power BI may auto-detect them.\n"

    instructions += "\n## Step 4: Create Measures\n\n"
    if measures:
        instructions += "In the Data pane, create these DAX measures:\n\n"
        for m in measures:
            instructions += f"""### {m['name']}
```dax
{m['dax_expression']}
```
Confidence: {m['confidence']:.0%}
"""
            if m["warnings"]:
                instructions += f"Warnings: {'; '.join(m['warnings'])}\n"
            instructions += "\n"
    else:
        instructions += "No calculated measures to create.\n"

    instructions += """
## Step 5: Build Visuals

Refer to migration_plan.md for the visual reconstruction order.
Use pbip_config.json if you want to import via PBIP tooling.

## Step 6: Apply Formatting

- Match color schemes from Tableau (see tableau_spec.json > worksheet_colors)
- Apply conditional formatting as documented
- Set up tooltips and drill-through pages

## Step 7: Publish

1. File > Publish > Publish to Power BI
2. Select your workspace
3. Configure scheduled refresh if using Import mode
"""

    return instructions


def _run_direct_mapper(tableau_spec: dict) -> Optional[dict]:
    """Run the direct mapper to produce PBIP config (if dashboards exist)."""
    if not tableau_spec.get("dashboards"):
        return None

    try:
        from core.direct_mapper import build_pbip_config_from_tableau
        # Direct mapper needs a data_profile -- build minimal one from spec
        data_profile = {"columns": [], "row_count": 0}
        for ds in tableau_spec.get("datasources", []):
            for col in ds.get("columns", []):
                if isinstance(col, str):
                    data_profile["columns"].append({"name": col, "dtype": "object"})
                elif isinstance(col, dict):
                    data_profile["columns"].append({
                        "name": col.get("name", ""),
                        "dtype": col.get("datatype", "object"),
                    })

        config = build_pbip_config_from_tableau(tableau_spec, data_profile)
        return config
    except Exception as e:
        return {"error": str(e)}


def execute(file_path: str, job_id: str) -> dict:
    """Run full Tableau-to-Power-BI migration analysis.

    Args:
        file_path: Path to .twb or .twbx file
        job_id: Job ID for artifact storage

    Returns:
        dict with status, artifacts list, and summary
    """
    from capability.artifact_store import save_artifact

    result = {
        "status": "success",
        "artifacts": [],
        "summary": {},
        "errors": [],
    }

    # Stage 1: Parse TWB
    try:
        tableau_spec = _parse_twb(file_path)
    except Exception as e:
        result["status"] = "error"
        result["errors"].append(f"TWB parsing failed: {str(e)}")
        return result

    # Save parsed spec
    path = save_artifact(job_id, "tableau_spec.json", tableau_spec)
    result["artifacts"].append("tableau_spec.json")

    # Stage 2: Transpile calculated fields to DAX
    calc_fields = tableau_spec.get("calculated_fields", [])
    datasources = tableau_spec.get("datasources", [])
    dax_translations = _transpile_calculated_fields(calc_fields, datasources)

    path = save_artifact(job_id, "dax_translations.json", dax_translations)
    result["artifacts"].append("dax_translations.json")

    # Stage 3: Build model mapping
    model_mapping = _build_model_mapping(tableau_spec, dax_translations)
    path = save_artifact(job_id, "model_mapping.json", model_mapping)
    result["artifacts"].append("model_mapping.json")

    # Stage 4: Build migration plan
    migration_plan = _build_migration_plan(tableau_spec, model_mapping, dax_translations)
    path = save_artifact(job_id, "migration_plan.md", migration_plan)
    result["artifacts"].append("migration_plan.md")

    # Stage 5: Build instructions
    instructions = _build_instructions(tableau_spec, model_mapping)
    path = save_artifact(job_id, "pbix_build_instructions.md", instructions)
    result["artifacts"].append("pbix_build_instructions.md")

    # Stage 6: Run direct mapper for PBIP config (if applicable)
    pbip_config = _run_direct_mapper(tableau_spec)
    if pbip_config and "error" not in pbip_config:
        path = save_artifact(job_id, "pbip_config.json", pbip_config)
        result["artifacts"].append("pbip_config.json")
    elif pbip_config and "error" in pbip_config:
        result["errors"].append(f"Direct mapper warning: {pbip_config['error']}")

    # Summary
    total_calc = len(dax_translations)
    ok_calc = sum(1 for t in dax_translations if t["parse_success"])
    result["summary"] = {
        "workbook_version": tableau_spec.get("version", ""),
        "datasources": len(datasources),
        "worksheets": len(tableau_spec.get("worksheets", [])),
        "dashboards": len(tableau_spec.get("dashboards", [])),
        "calculated_fields_total": total_calc,
        "dax_translated_ok": ok_calc,
        "dax_needs_review": total_calc - ok_calc,
        "has_pbip_config": "pbip_config.json" in result["artifacts"],
        "parameters": len(tableau_spec.get("parameters", [])),
        "filters": len(tableau_spec.get("filters", [])),
    }

    return result
