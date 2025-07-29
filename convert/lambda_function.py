import json
import os
import boto3
import logging
import tempfile
import rasterio
from datetime import datetime
from pathlib import Path
import re
from collections import Counter
from botocore.config import Config  # ✅ Corrección clave

# === Configuración inicial ===
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Configuración de timeout para evitar esperas largas
config = Config(connect_timeout=10, read_timeout=10)

s3 = boto3.client("s3", config=config, region_name="us-west-2")
sns = boto3.client("sns", config=config, region_name="us-west-2")

S3_BUCKET = os.environ.get("S3_BUCKET")
SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN")

# Usar EFS como almacenamiento temporal
EFS_TEMP_DIR = "/mnt/efs/temp-cog"
os.makedirs(EFS_TEMP_DIR, exist_ok=True)

# === Convertir TIFF multibanda a Cloud Optimized GeoTIFF usando rio_cogeo ===
def multiband_tiff_to_cog_stream(input_path, output_path):
    try:
        from rio_cogeo.cogeo import cog_translate
        from rio_cogeo.profiles import cog_profiles

        logger.info(f"Convirtiendo a COG: {input_path} -> {output_path}")

        # Perfil COG con ZSTD
        profile = cog_profiles.get("deflate")
        profile.update({
            "compress": "ZSTD",
            "zstd_level": 6,
            "blocksize": 512
        })

        cog_translate(
            src_path=input_path,
            dst_path=output_path,
            profile=profile,
            in_memory=False,
            quiet=False
        )
        logger.info(f"✅ COG generado exitosamente: {output_path}")
    except ImportError:
        logger.error("rio_cogeo no está disponible. Asegúrate de incluirlo en la capa.")
        raise
    except Exception as e:
        logger.exception(f"Error al generar COG con rio_cogeo: {e}")
        raise

# === Extraer fecha de composite_metadato.json ===
def get_date_from_metadata(tif_key: str) -> str:
    """
    Extrae la fecha de composite_metadato.json y la formatea como YYYYMMDD.
    Busca el archivo en la misma carpeta que el TIFF.
    """
    # Ruta del archivo de metadatos
    folder_path = os.path.dirname(tif_key)
    metadata_key = f"{folder_path}/composite_metadato.json"

    try:
        response = s3.get_object(Bucket=S3_BUCKET, Key=metadata_key)
        meta_data = json.loads(response["Body"].read())
        acquired = meta_data.get("properties", {}).get("acquired", "")

        if not acquired:
            raise ValueError("No se encontró 'acquired' en composite_metadato.json")

        # Extraer fecha antes de la 'T'
        date_str = acquired.split("T")[0]  # Ej: "2023-06-20"
        # Convertir a YYYYMMDD
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        formatted_date = date_obj.strftime("%Y%m%d")  # Ej: "20230620"
        logger.info(f"Fecha extraída de {metadata_key}: {formatted_date}")
        return formatted_date

    except Exception as e:
        logger.warning(f"No se pudo leer composite_metadato.json: {str(e)}. Usando fecha de archivo.")
        # Fallback: usar nombre del archivo
        base_name = os.path.basename(tif_key)
        if "composite" in base_name.lower():
            return "99999999"  # Valor por defecto si no hay metadatos
        raise

# === Renombrar COGs según fecha de adquisición ===
def rename_cog_files_with_acquisition_date(bucket_name: str, folder_path: str, dry_run: bool = True):
    """
    Renombra composite_COG.tif y composite_udm2_COG.tif basado en la fecha de adquisición.
    """
    logger.info(f"Intentando renombrar COGs en: s3://{bucket_name}/{folder_path} (dry_run={dry_run})")
    try:
        response = s3.list_objects_v2(Bucket=bucket_name, Prefix=folder_path)
        if 'Contents' not in response:
            logger.warning(f"No se encontraron archivos en {folder_path}")
            return {"error": "No files found in the specified folder"}

        file_names = [obj['Key'].split('/')[-1] for obj in response['Contents']]
        logger.debug(f"Archivos encontrados: {file_names}")

        # Patrón: YYYYMMDD_hhmmss
        date_pattern = r'(\d{8})_\d{6}'

        acquisition_dates = []
        for name in file_names:
            matches = re.findall(date_pattern, name)
            acquisition_dates.extend(matches)

        date_counts = Counter(acquisition_dates)
        logger.debug(f"Fechas encontradas: {dict(date_counts)}")

        if not date_counts:
            logger.warning("No se encontró ningún patrón de fecha en los nombres de archivo")
            return {"error": "No acquisition date pattern found"}

        if len(date_counts) > 1:
            logger.error(f"Múltiples fechas encontradas: {list(date_counts.keys())}")
            return {
                "error": f"Multiple dates found: {list(date_counts.keys())}. Expected one."
            }

        acquisition_date = list(date_counts.keys())[0]
        logger.info(f"Fecha de adquisición detectada: {acquisition_date}")

        # Buscar archivos a renombrar
        target_files = {}
        for obj in response['Contents']:
            key = obj['Key']
            filename = key.split('/')[-1]
            if filename == 'composite_COG.tif':
                target_files['composite_COG.tif'] = key
            elif filename == 'composite_udm2_COG.tif':
                target_files['composite_udm2_COG.tif'] = key

        if not target_files:
            logger.warning("No se encontraron archivos composite_COG.tif o composite_udm2_COG.tif")
            return {"error": "No composite_COG.tif or composite_udm2_COG.tif found"}

        operations = []
        for old_name, old_key in target_files.items():
            suffix = "UDM2_COG.tif" if "udm2" in old_name else "COG.tif"
            new_name = f"{acquisition_date}_{suffix}"
            new_key = '/'.join(old_key.split('/')[:-1] + [new_name])
            operations.append({'old_key': old_key, 'new_key': new_key, 'new_name': new_name})

        if dry_run:
            logger.info(f"DRY RUN: Se renombraría {len(operations)} archivo(s):")
            for op in operations:
                logger.info(f"  {op['old_key']} -> {op['new_key']}")
            return {"acquisition_date": acquisition_date, "operations": operations, "dry_run": True}

        success_count = 0
        errors = []
        for op in operations:
            try:
                logger.info(f"Renombrando: {op['old_key']} -> {op['new_key']}")
                s3.copy_object(
                    Bucket=bucket_name,
                    CopySource={'Bucket': bucket_name, 'Key': op['old_key']},
                    Key=op['new_key']
                )
                s3.delete_object(Bucket=bucket_name, Key=op['old_key'])
                logger.info(f"✅ Renombrado exitoso: {op['new_name']}")
                success_count += 1
            except Exception as e:
                error_msg = f"❌ Fallo al renombrar {op['old_key']}: {str(e)}"
                errors.append(error_msg)
                logger.error(error_msg)

        return {
            "acquisition_date": acquisition_date,
            "success_count": success_count,
            "errors": errors
        }

    except Exception as e:
        logger.exception(f"Error crítico en rename_cog_files: {str(e)}")
        return {"error": str(e)}

# === Función Lambda principal ===
def lambda_handler(event, context):
    logger.info(f"=== Inicio de ejecución de Lambda convert-to-cog ===")
    logger.info(f"Request ID: {context.aws_request_id}")
    logger.info(f"Event structure: {list(event.keys())}")
    if 'Records' in event:
        logger.info(f"  Records count: {len(event['Records'])}")
        if 's3' in event['Records'][0]:
            logger.info(f"  S3 Event detected")
        elif 'body' in event['Records'][0]:
            logger.info(f"  SQS Event detected")

    bucket = S3_BUCKET
    if not bucket:
        logger.error("Falta variable de entorno S3_BUCKET")
        return {"statusCode": 500, "body": "S3_BUCKET no configurado"}

    key = None
    try:
        if "Records" in event:
            record = event["Records"][0]
            if "s3" in record and "object" in record["s3"]:
                key = record["s3"]["object"]["key"]
            elif "body" in record:
                body = json.loads(record["body"])
                if "detail" in body:
                    key = body["detail"]["object"]["key"]
                elif "s3" in body:
                    key = body["s3"]["object"]["key"]
        elif "detail" in event:
            key = event["detail"]["object"]["key"]
        elif "log_key" in event:
            key = event["log_key"]

        if not key:
            raise KeyError("No se pudo extraer 'key' del evento")

        # Limpiar espacios
        key = key.strip()
        logger.info(f"Key del archivo de log: {key}")
    except Exception as e:
        logger.error(f"Evento malformado o no compatible: {str(e)}")
        return {"statusCode": 400, "body": "Evento no válido"}

    if not key.endswith("unpacked_info.json"):
        logger.info(f"Ignorando archivo no relevante: {key}")
        return {"statusCode": 200, "body": "Ignorado"}

    logger.info(f"Procesando: s3://{bucket}/{key}")

    try:
        response = s3.get_object(Bucket=bucket, Key=key)
        log_data = json.loads(response["Body"].read())
        logger.info(f"Log JSON leído exitosamente ({len(json.dumps(log_data))} bytes)")
    except Exception as e:
        logger.exception(f"No se pudo leer {key}: {str(e)}")
        return {"statusCode": 500, "body": "Error al leer metadata"}

    tiff_files = log_data.get("files", []) or log_data.get("tiff_files", [])
    if not tiff_files:
        logger.warning(f"No se encontraron archivos TIFF en {key}")
        return {"statusCode": 200, "body": "Sin archivos TIFF"}

    logger.info(f"Archivos TIFF encontrados: {len(tiff_files)}")
    for tif_key in tiff_files:
        logger.info(f"  - {tif_key}")

    processed = []
    failed = []

    # Directorio temporal en EFS
    temp_dir = tempfile.TemporaryDirectory(dir=EFS_TEMP_DIR)
    tmpdir = temp_dir.name
    logger.info(f"Directorio temporal creado en EFS: {tmpdir}")

    try:
        for tif_key in tiff_files:
            try:
                logger.info(f"Procesando archivo TIFF: {tif_key}")
                input_path = os.path.join(tmpdir, "input.tif")
                output_path = os.path.join(tmpdir, "output_cog.tif")

                # Descargar
                logger.debug(f"Descargando {tif_key} a {input_path}")
                s3.download_file(bucket, tif_key, input_path)
                logger.info(f"✅ Descargado: {input_path} ({os.path.getsize(input_path)} bytes)")

                # Convertir a COG
                multiband_tiff_to_cog_stream(input_path, output_path)
                logger.info(f"✅ COG generado: {output_path} ({os.path.getsize(output_path)} bytes)")

                # Generar nombre
                try:
                    date_prefix = get_date_from_metadata(tif_key)
                except Exception as e:
                    logger.warning(f"Error al obtener fecha de metadatos: {str(e)}")
                    date_prefix = "99999999"  # Valor por defecto

                base_name = os.path.basename(tif_key)
                if "composite" in base_name.lower():
                    new_name = f"{date_prefix}_composite_{context.aws_request_id[:8]}_cog.tif"
                else:
                    clean_name = Path(base_name).stem
                    new_name = f"{date_prefix}_{clean_name}_cog.tif"

                cog_key = f"{os.path.dirname(tif_key)}/{new_name}"

                # Subir
                logger.debug(f"Subiendo COG a S3: {cog_key}")
                s3.upload_file(output_path, bucket, cog_key)
                logger.info(f"✅ COG subido: s3://{bucket}/{cog_key}")
                processed.append(cog_key)

            except Exception as e:
                logger.exception(f"❌ Fallo con {tif_key}: {str(e)}")
                failed.append({"file": tif_key, "error": str(e)})

        # === Renombrar COGs si es necesario ===
        folder_path = os.path.dirname(tif_key).rstrip("/") + "/"
        logger.info(f"Intentando renombrar COGs en carpeta: {folder_path}")
        rename_result = rename_cog_files_with_acquisition_date(bucket, folder_path, dry_run=False)

        # === Publicar a SNS ===
        result = {
            "status": "done",
            "request_id": context.aws_request_id,
            "input_log": key,
            "processed": len(processed),
            "failed": len(failed),
            "cog_files": processed,
            "renaming_result": rename_result
        }

        logger.info(f"Resultado final: {json.dumps(result, indent=2)[:500]}...")

        if SNS_TOPIC_ARN:
            try:
                sns_message = json.dumps(result)
                logger.debug(f"Publicando mensaje SNS ({len(sns_message)} bytes) a {SNS_TOPIC_ARN}")
                sns.publish(
                    TopicArn=SNS_TOPIC_ARN,
                    Subject="cog-complete",
                    Message=sns_message
                )
                logger.info(f"✅ Publicado resumen a SNS: {SNS_TOPIC_ARN}")
            except Exception as e:
                logger.exception(f"❌ Error al publicar en SNS: {e}")
        else:
            logger.warning("SNS_TOPIC_ARN no está definido")

        return {"statusCode": 200, "body": result}

    except Exception as e:
        logger.exception(f"Error crítico en lambda_handler: {e}")
        return {"statusCode": 500, "body": "Internal error"}

    finally:
        # === Limpieza de recursos ===
        try:
            temp_dir.cleanup()
            logger.info(f"🧹 Carpeta temporal eliminada: {tmpdir}")
        except Exception as e:
            logger.warning(f"⚠️ No se pudo eliminar la carpeta temporal {tmpdir}: {str(e)}")

        logger.info("=== Fin de ejecución de Lambda convert-to-cog ===")