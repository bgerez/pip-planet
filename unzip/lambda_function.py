import boto3
import os
import zipfile
import uuid
import json
import logging
import time
import shutil  # 👈 para eliminar carpetas recursivamente

# Configurar logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Clientes AWS
s3 = boto3.client('s3')
sns = boto3.client('sns')

# Variables de entorno
SNS_TOPIC_ARN = os.environ.get('SNS_TOPIC_ARN')
MAX_SIZE_MB = int(os.environ.get('MAX_TOTAL_UNZIPPED_SIZE_MB', 20480))
MAX_SIZE_BYTES = MAX_SIZE_MB * 1024 * 1024

# Retardos para reintento (si el archivo aún no está listo)
MAX_RETRIES = 3
RETRY_DELAY = 5  # segundos


def lambda_handler(event, context):
    for record in event.get('Records', []):
        tmp_zip_path = None
        extract_dir = None

        try:
            # Parsear el evento SQS
            try:
                sqs_body = json.loads(record['body'])
                if 'detail' in sqs_body:
                    detail = sqs_body['detail']
                else:
                    message = sqs_body.get('Message', '{}')
                    nested = json.loads(message)
                    detail = nested.get('detail', {})
            except Exception as e:
                logger.error(f"Error al parsear evento SQS: {str(e)}")
                raise

            bucket = detail.get('bucket', {}).get('name')
            key = detail.get('object', {}).get('key')

            if not bucket or not key:
                logger.error("Evento malformado: falta bucket o key")
                continue

            logger.info(f"Procesando archivo ZIP: s3://{bucket}/{key}")

            # Validar que el objeto existe
            file_size = None
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    head = s3.head_object(Bucket=bucket, Key=key)
                    file_size = head['ContentLength']
                    if file_size > 0:
                        logger.info(f"Archivo listo para descargar. Tamaño: {file_size} bytes")
                        break
                    else:
                        logger.warning(f"Intento {attempt}: archivo {key} con tamaño 0")
                except Exception as e:
                    logger.warning(f"Intento {attempt}: error al verificar objeto: {str(e)}")

                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY)
                else:
                    raise Exception(f"Archivo no disponible luego de {MAX_RETRIES} intentos")

            # Descargar archivo
            tmp_zip_path = f"/tmp/{uuid.uuid4()}.zip"
            logger.info(f"Descargando {key} a {tmp_zip_path}")
            s3.download_file(bucket, key, tmp_zip_path)

            # Validar ZIP
            logger.info("Validando ZIP...")
            with zipfile.ZipFile(tmp_zip_path, 'r') as zip_ref:
                corrupt_file = zip_ref.testzip()
                if corrupt_file:
                    raise Exception(f"Archivo ZIP corrupto: {corrupt_file}")

            extract_dir = f"/tmp/{uuid.uuid4()}-unzipped"
            os.makedirs(extract_dir, exist_ok=True)

            extracted_files = []
            skipped_files = []
            tiff_files = []
            total_unzipped_size = 0

            with zipfile.ZipFile(tmp_zip_path, 'r') as zip_ref:
                for file_info in zip_ref.infolist():
                    if total_unzipped_size + file_info.file_size > MAX_SIZE_BYTES:
                        logger.warning(f"Saltando archivo: {file_info.filename}")
                        skipped_files.append({
                            "filename": file_info.filename,
                            "size_mb": round(file_info.file_size / (1024 * 1024), 2),
                            "reason": "exceeds_max_total_unzipped_size"
                        })
                        continue

                    zip_ref.extract(file_info, extract_dir)
                    total_unzipped_size += file_info.file_size

                    rel_path = file_info.filename
                    s3_key = f"{key.replace('.zip', '')}/{rel_path}"

                    local_file = os.path.join(extract_dir, rel_path)
                    logger.info(f"Subiendo {s3_key} a S3")
                    s3.upload_file(local_file, bucket, s3_key)
                    extracted_files.append(s3_key)

                    if file_info.filename.lower().endswith(('.tif', '.tiff')):
                        tiff_files.append(s3_key)

            # Metadata
            unzip_folder = key.replace(".zip", "")
            unpacked_info = {
                "unpacked_to": f"{unzip_folder}/",
                "files": sorted(tiff_files)
            }
            unpacked_info_key = f"{unzip_folder}/unpacked_info.json"
            logger.info(f"Subiendo metadata: {unpacked_info_key}")
            try:
                s3.put_object(
                    Bucket=bucket,
                    Key=unpacked_info_key,
                    Body=json.dumps(unpacked_info, indent=2),
                    ContentType="application/json"
                )
            except Exception as e:
                logger.error(f"Error al subir metadata: {str(e)}")

            # Notificación SNS
            success_message = {
                "status": "success",
                "bucket": bucket,
                "original_zip": key,
                "unzipped_to": unzip_folder,
                "total_extracted_files": len(extracted_files),
                "total_skipped_files": len(skipped_files),
                "total_size_mb": round(total_unzipped_size / (1024 * 1024), 2),
                "extracted_files": sorted(extracted_files),
                "skipped_files": skipped_files,
                "tiff_files": sorted(tiff_files)
            }

            sns.publish(
                TopicArn=SNS_TOPIC_ARN,
                Subject=f"✅ ZIP procesado: {os.path.basename(key)}",
                Message=json.dumps(success_message, indent=2)
            )

            logger.info(f"Procesado exitosamente: {key}")

        except Exception as e:
            logger.exception(f"Error en la ejecución principal: {str(e)}")
            error_message = {
                "status": "error",
                "bucket": bucket,
                "key": key,
                "error": str(e),
                "event_id": record.get('eventID')
            }
            try:
                sns.publish(
                    TopicArn=SNS_TOPIC_ARN,
                    Subject=f"❌ ERROR al procesar ZIP: {os.path.basename(key)}",
                    Message=json.dumps(error_message, indent=2)
                )
            except Exception as sns_error:
                logger.error(f"Error al enviar mensaje SNS de error: {str(sns_error)}")
            raise

        finally:
            # 🧹 Eliminar archivos temporales si existen
            if tmp_zip_path and os.path.exists(tmp_zip_path):
                os.remove(tmp_zip_path)
                logger.info(f"Eliminado archivo ZIP temporal: {tmp_zip_path}")

            if extract_dir and os.path.exists(extract_dir):
                shutil.rmtree(extract_dir, ignore_errors=True)
                logger.info(f"Eliminado directorio descomprimido temporal: {extract_dir}")

    return {"statusCode": 200, "body": "Procesado"}
