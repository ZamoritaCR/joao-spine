# Power BI Build Instructions

## Prerequisites
- Power BI Desktop (latest version)
- Access to original data source(s)
- This artifact bundle (all JSON files)

## Step 1: Create New Report

1. Open Power BI Desktop
2. Save as your project name

## Step 2: Connect Data Sources

### Source 1: Quarterly Sales Data (DigitalAds-Sales-Data)
- Original connection type: **federated**
- In Power BI: Home > Get Data > choose appropriate connector
- Load all required tables

### Source 2: Quarterly Sales Data (DigitalAds-Sales-Data)
- Original connection type: ****
- In Power BI: Home > Get Data > choose appropriate connector
- Load all required tables

### Source 3: Quarterly Sales Data (DigitalAds-Sales-Data)
- Original connection type: ****
- In Power BI: Home > Get Data > choose appropriate connector
- Load all required tables

### Source 4: Quarterly Sales Data (DigitalAds-Sales-Data)
- Original connection type: ****
- In Power BI: Home > Get Data > choose appropriate connector
- Load all required tables

### Source 5: Quarterly Sales Data (DigitalAds-Sales-Data)
- Original connection type: ****
- In Power BI: Home > Get Data > choose appropriate connector
- Load all required tables

### Source 6: Quarterly Sales Data (DigitalAds-Sales-Data)
- Original connection type: ****
- In Power BI: Home > Get Data > choose appropriate connector
- Load all required tables

## Step 3: Set Up Relationships

No explicit relationships found -- Power BI may auto-detect them.

## Step 4: Create Measures

In the Data pane, create these DAX measures:

### [Number of Records]
```dax
1
```
Confidence: 100%


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
