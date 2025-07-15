# Lambda Functions Documentation

This document describes the two AWS Lambda functions developed to automate geospatial data processing in an S3-based pipeline. Both are designed to be serverless, efficient, and easy to maintain.

---

## ✨ Function 1: `unzip-s3-files`

### 🔖 Purpose

Searches for `.zip` files within a specified S3 prefix (folder path), extracts the contents, and uploads the unzipped files back into S3 while preserving directory structure.

### ⚙️ Workflow

1. Receives an event input with a key: `"prefix"`, e.g.:

   ```json
   {
     "prefix": "2025/ATLANTICO/"
   }
   ```

2. Lists all `.zip` files under the specified prefix using `list_objects_v2`.

3. For each `.zip` file:

   - Downloads the `.zip` to `/tmp`.
   - Unzips the content using Python's `zipfile` module.
   - Uploads each extracted file back to the same S3 path where the `.zip` was found.

### 🚀 Key Benefits

- Supports multiple `.zip` files and nested folder structures.
- Automatically preserves and rebuilds the S3 structure.
- Works efficiently within Lambda's temporary storage constraints.

---

## ✨ Function 2: `convert-to-cog`

### 🔖 Purpose

Converts multiband GeoTIFF files into Cloud Optimized GeoTIFF (COG) format using `rasterio` and `rio-cogeo`.

### ⚙️ Workflow

1. Receives an event input with a key: `"log_key"`, which is a path to a `log.json` file stored in S3. This JSON includes a list of `.tif` files:

   ```json
   {
     "files": [
       "2025/ATLANTICO/some_folder/composite.tif",
       "2025/ATLANTICO/some_folder/composite_udm2.tif"
     ]
   }
   ```

2. For each `.tif` file:

   - Downloads the file to `/tmp/input.tif`.
   - Converts it to COG using block-based streaming (no full memory read) via `cog_translate()`.
   - Saves the result to `/tmp/output_cog.tif`.
   - Uploads the resulting `*_COG.tif` back to the same S3 folder.

### ⚖️ Technical Details

- Uses a Lambda Layer with `rasterio`, `rio-cogeo`, and `numpy`, built via Docker.
- Uses ephemeral storage of 10GB (`ephemeral_storage.size = 10240`) to handle large raster files.
- Compression method: `ZSTD` (default).

### 🚀 Key Benefits

- Enables on-demand conversion of large raster files.
- Optimizes files for efficient access from cloud-based tools.
- Minimizes memory usage using efficient streaming logic.

---

## 📚 Deployment Notes

Both functions:

- Are deployed via Terraform.
- Include appropriate IAM roles for access to:
  - S3 buckets (read/write).
  - CloudWatch Logs.
- Use `publish = true` for versioning and can be aliased for staging/production.

---

## ✉️ Contact

For questions, updates, or issues, please contact the infrastructure maintainer or the data engineering team.

---

*Generated on 2025-07-15*

---

