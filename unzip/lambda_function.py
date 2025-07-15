import boto3
import zipfile
import os
import json

s3 = boto3.client('s3')

def lambda_handler(event, context):
    bucket = event["bucket"]
    key = event["key"]

    print(f"Processing ZIP: {key}")

    tmp_zip_path = f"/tmp/{os.path.basename(key)}"

    # Descargar ZIP a /tmp
    s3.download_file(bucket, key, tmp_zip_path)

    # Crear carpeta destino en /tmp
    unzip_dir = tmp_zip_path.replace('.zip', '')
    os.makedirs(unzip_dir, exist_ok=True)

    # Extraer contenido
    with zipfile.ZipFile(tmp_zip_path, 'r') as zip_ref:
        zip_ref.extractall(unzip_dir)

    extracted_files = []

    # Subir archivos extraídos a S3 y recolectar nombres
    for root, _, files in os.walk(unzip_dir):
        for filename in files:
            full_path = os.path.join(root, filename)
            relative_path = os.path.relpath(full_path, unzip_dir)
            s3_key_out = os.path.join(key.replace(".zip", ""), relative_path)

            s3.upload_file(full_path, bucket, s3_key_out)
            print(f"Uploaded: {s3_key_out}")
            extracted_files.append(s3_key_out)

    # Filtrar solo los .tif si deseas limitar el log
    tif_files = [f for f in extracted_files if f.lower().endswith('.tif')]

    # Crear log con lista de archivos
    log_key = key.replace(".zip", "/log.json")
    log_body = json.dumps({
        "unpacked_to": key.replace(".zip", "/"),
        "files": tif_files
    })

    s3.put_object(Bucket=bucket, Key=log_key, Body=log_body)

    return {
        "status": "success",
        "log_key": log_key
    }
