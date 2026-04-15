# Power BI Build Instructions

## Prerequisites
- Power BI Desktop (latest version)
- Access to original data source(s)
- This artifact bundle (all JSON files)

## Step 1: Create New Report

1. Open Power BI Desktop
2. Save as your project name

## Step 2: Connect Data Sources

## Step 3: Set Up Relationships

No explicit relationships found -- Power BI may auto-detect them.

## Step 4: Create Measures

No calculated measures to create.

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
