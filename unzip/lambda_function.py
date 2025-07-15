import boto3
import zipfile
import os
import json

s3 = boto3.client('s3')
sns = boto3.client('sns')

MAX_ZIP_SIZE_BYTES = 10 * 1024 * 1024 * 1024  # 10 GB

def lambda_handler(event, context):
    bucket = event["Records"][0]["s3"]["bucket"]["name"]
    key = event["Records"][0]["s3"]["object"]["key"]

    if not key.lower().endswith(".zip"):
        print(f"[DEBUG] Ignorado: {key}")
        return {"status": "ignored"}

    prefix = os.path.dirname(key) + "/"
    sns_topic_arn = os.environ.get("SNS_TOPIC_ARN")

    print(f"[DEBUG] Bucket: {bucket}")
    print(f"[DEBUG] Prefijo: {prefix}")

    paginator = s3.get_paginator("list_objects_v2")
    zip_keys = []

    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            k = obj["Key"]
            if k.lower().endswith(".zip"):
                zip_keys.append(k)

    print(f"[DEBUG] ZIPs detectados: {zip_keys}")

    results = []

    for zip_key in zip_keys:
        print(f"[DEBUG] Procesando ZIP: {zip_key}")
        result = {"zip_file": zip_key, "status": "", "extracted_files": [], "error": None}

        try:
            head = s3.head_object(Bucket=bucket, Key=zip_key)
            size = head["ContentLength"]
            result["size_MB"] = round(size / (1024 * 1024), 2)

            if size > MAX_ZIP_SIZE_BYTES:
                result["status"] = "omitted (size > 10GB)"
                print(f"[WARNING] {zip_key} omitido por superar los 10GB")
                results.append(result)
                continue
        except Exception as e:
            result["status"] = "error"
            result["error"] = f"Error al obtener metadata: {str(e)}"
            results.append(result)
            continue

        try:
            tmp_zip_path = f"/tmp/{os.path.basename(zip_key)}"
            s3.download_file(bucket, zip_key, tmp_zip_path)

            unzip_dir = tmp_zip_path.replace(".zip", "")
            os.makedirs(unzip_dir, exist_ok=True)

            with zipfile.ZipFile(tmp_zip_path, 'r') as zip_ref:
                zip_ref.extractall(unzip_dir)

            for root, _, files in os.walk(unzip_dir):
                for filename in files:
                    full_path = os.path.join(root, filename)
                    relative_path = os.path.relpath(full_path, unzip_dir)
                    s3_key_out = os.path.join(zip_key.replace(".zip", ""), relative_path)

                    s3.upload_file(full_path, bucket, s3_key_out)
                    result["extracted_files"].append({
                        "s3_key": s3_key_out,
                        "size_MB": round(os.path.getsize(full_path) / (1024 * 1024), 2)
                    })
                    print(f"[DEBUG] Subido: {s3_key_out}")

            result["status"] = "success"

            # ✅ Crear y subir log.json
            tif_files = [
                f["s3_key"] for f in result["extracted_files"]
                if f["s3_key"].lower().endswith(".tif")
            ]
            if tif_files:
                log_content = json.dumps({"files": tif_files}, indent=2)
                log_path = os.path.join("/tmp", "log.json")
                with open(log_path, "w") as f:
                    f.write(log_content)

                log_s3_key = os.path.join(zip_key.replace(".zip", ""), "log.json")
                s3.upload_file(log_path, bucket, log_s3_key)
                print(f"[DEBUG] log.json subido en: {log_s3_key}")

        except Exception as e:
            result["status"] = "error"
            result["error"] = str(e)

        results.append(result)

    # ✅ Publicar resumen por SNS
    summary = {
        "bucket": bucket,
        "prefix": prefix,
        "results": results
    }

    sns.publish(
        TopicArn=sns_topic_arn,
        Subject=f"[Lambda] Resultados unzip S3 - {prefix}",
        Message=json.dumps(summary, indent=2)
    )

    return {
        "status": "done",
        "results": results
    }
    print("[DEBUG] Proceso completado")     