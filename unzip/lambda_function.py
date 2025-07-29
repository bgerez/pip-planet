import json
import os
import uuid
import boto3
import logging
import shutil  # Necesario para eliminar carpetas recursivamente
from pathlib import Path
from botocore.config import Config
import zipfile  # ¡Importante! Faltaba importar zipfile

# === Configuración inicial ===
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Configuración de timeout para evitar esperas largas
config = Config(connect_timeout=10, read_timeout=10)

s3 = boto3.client("s3", config=config, region_name="us-west-2")
sns = boto3.client("sns", config=config, region_name="us-west-2")

SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN")
EFS_BASE_DIR = os.environ.get("EFS_BASE_DIR", "/mnt/efs/lambda-unzip")
logger.info(f"Usando EFS base dir: {EFS_BASE_DIR}")


def lambda_handler(event, context):
    logger.info("Inicio de ejecución de Lambda unzip")

    for record in event.get("Records", []):
        # Inicializar la variable para la carpeta temporal
        unzip_dir = None

        try:
            # --- Determinar tipo de evento ---
            message = None

            if record.get("eventSource") == "aws:sqs":
                logger.info("Evento recibido desde SQS")
                body = json.loads(record["body"])
                if isinstance(body.get("Message"), str):
                    message = json.loads(body["Message"])
                else:
                    message = body
            elif record.get("EventSource") == "aws:sns" or "Sns" in record:
                logger.info("Evento recibido desde SNS")
                message = json.loads(record["Sns"]["Message"])
            elif record.get("eventSource") == "aws:s3":
                logger.info("Evento recibido directamente desde S3")
                message = record
            else:
                logger.warning("Tipo de evento no reconocido")
                continue

            # --- Obtener bucket y key ---
            detail = message.get("detail", {})
            bucket = detail.get("bucket", {}).get("name", message.get("bucket"))
            key = detail.get("object", {}).get("key", message.get("key"))

            if not bucket or not key:
                logger.warning("No se pudo obtener bucket y key")
                continue

            # Eliminar espacios o caracteres extraños
            key = key.strip()
            logger.info(f"Bucket: {bucket}")
            logger.info(f"Key del ZIP: {key}")
            s3_path = f"s3://{bucket}/{key}"
            logger.info(f"Verificando disponibilidad del archivo: {s3_path}")

            # --- Crear carpeta de trabajo en EFS ---
            unzip_dir = Path(EFS_BASE_DIR) / str(uuid.uuid4())
            unzip_dir.mkdir(parents=True, exist_ok=True)

            # --- Descargar ZIP al EFS ---
            tmp_zip_path = unzip_dir / "archivo.zip"
            s3.download_file(bucket, key, str(tmp_zip_path))
            logger.info(f"ZIP descargado en {tmp_zip_path}")

            # --- Descomprimir archivos en EFS ---
            extracted_files = []
            with zipfile.ZipFile(tmp_zip_path, "r") as zip_ref:
                zip_ref.extractall(unzip_dir)
                extracted_files = [f.lstrip("./") for f in zip_ref.namelist()]

            logger.info(f"Archivos extraídos: {extracted_files}")

            # --- Subir archivos a S3 ---
            output_prefix = os.path.splitext(key)[0]  # Ej: "2025/Risaralda/..."
            uploaded_files = []

            for filename in extracted_files:
                local_path = unzip_dir / filename
                if local_path.is_file():
                    s3_key = f"{output_prefix}/{filename}"
                    s3.upload_file(str(local_path), bucket, s3_key)
                    uploaded_files.append(s3_key)

            logger.info(f"Archivos subidos a S3: {uploaded_files}")

            uploaded_tif_files = [f for f in uploaded_files if f.lower().endswith(".tif")]

            # --- Guardar resumen JSON en nuevo formato ---
            unpacked_to = f"{output_prefix}/"
            resumen = {
                "unpacked_to": unpacked_to,
                "files": uploaded_tif_files
            }

            resumen_path = unzip_dir / "unpacked_info.json"
            with open(resumen_path, "w", encoding="utf-8") as f:
                json.dump(resumen, f, indent=2, ensure_ascii=False)

            resumen_s3_key = f"{output_prefix}/unpacked_info.json"
            s3.upload_file(str(resumen_path), bucket, resumen_s3_key)
            logger.info(f"Resumen guardado como: s3://{bucket}/{resumen_s3_key}")

            # --- Publicar a SNS ---
            if SNS_TOPIC_ARN:
                try:
                    sns.publish(
                        TopicArn=SNS_TOPIC_ARN,
                        Subject="unzip-complete",
                        Message=json.dumps(resumen)
                    )
                    logger.info(f"Publicado resumen a SNS: {SNS_TOPIC_ARN}")
                except Exception as e:
                    logger.exception(f"Error al publicar en SNS: {e}")
            else:
                logger.warning("SNS_TOPIC_ARN no está definido")

        except Exception as e:
            logger.exception(f"Error procesando el evento: {e}")
        finally:
            # === Eliminar carpeta temporal en EFS si fue creada ===
            if unzip_dir and unzip_dir.exists():
                try:
                    logger.info(f"Eliminando carpeta temporal: {unzip_dir}")
                    shutil.rmtree(unzip_dir)  # Elimina recursivamente
                    logger.info(f"Carpeta temporal eliminada: {unzip_dir}")
                except Exception as e:
                    logger.error(f"No se pudo eliminar la carpeta temporal {unzip_dir}: {e}")

    logger.info("Fin de ejecución Lambda unzip")
    return {"statusCode": 200, "body": "Procesamiento completado"}