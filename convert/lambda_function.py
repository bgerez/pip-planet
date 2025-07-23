import os
import json
import boto3
import tempfile
import logging
from datetime import datetime
from rio_cogeo.cogeo import cog_translate
from rio_cogeo.errors import RioCogeoError

# Configurar logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Clientes AWS
s3 = boto3.client('s3')

# Variables de entorno
S3_BUCKET = os.environ.get('S3_BUCKET')
COMPRESS = os.environ.get('COG_COMPRESSION', 'ZSTD')
BLOCK_SIZE = int(os.environ.get('COG_BLOCK_SIZE', 1024))


def multiband_tiff_to_cog_stream(input_path: str, output_path: str):
    """
    Convierte un GeoTIFF multibanda a COG con perfil optimizado.
    Usa rio-cogeo para garantizar compatibilidad con la nube.
    """
    logger.info(f"Convirtiendo {input_path} → {output_path} con compresión {COMPRESS}")

    cog_profile = {
        "driver": "GTiff",
        "tiled": True,
        "blockxsize": BLOCK_SIZE,
        "blockysize": BLOCK_SIZE,
        "compress": COMPRESS,
        "predictor": 2 if COMPRESS in ["LZW", "DEFLATE"] else 1,
        "interleave": "band",
        "photometric": "RGB" if COMPRESS == "JPEG" else "minisblack",
        "zlevel": 6 if COMPRESS == "ZSTD" else None,
        "num_threads": "ALL_CPUS",
    }

    try:
        with rasterio.open(input_path, "r") as src:
            cog_translate(
                src,
                output_path,
                dst_kwargs=cog_profile,
                overview_resampling="average",
                force=True
            )
        logger.info(f"✅ COG generado: {output_path}")
    except RioCogeoError as e:
        logger.error(f"Error de validación COG: {str(e)}")
        raise
    except Exception as e:
        logger.error(f"Error al convertir {input_path}: {str(e)}")
        raise


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


def lambda_handler(event, context):
    # Obtener bucket desde variable de entorno
    bucket = S3_BUCKET
    if not bucket:
        logger.error("Falta variable de entorno S3_BUCKET")
        return {"statusCode": 500, "body": "S3_BUCKET no configurado"}

    # Parsear evento: espera un S3 Put en unpacked_info.json
    try:
        if "Records" in event:
            record = event["Records"][0]
            if "s3" in record:
                key = record["s3"]["object"]["key"]
            elif "detail" in record:
                detail = record["detail"]
                key = detail["object"]["key"]
            else:
                raise KeyError("No se encontró clave en el evento")
        else:
            key = event.get("log_key")
            if not key:
                raise KeyError("log_key no proporcionado")
    except Exception as e:
        logger.error(f"Evento malformado: {str(e)}")
        return {"statusCode": 400, "body": "Evento no válido"}

    # Asegurar que sea unpacked_info.json
    if not key.endswith("unpacked_info.json"):
        logger.info(f"Ignorando archivo no relevante: {key}")
        return {"statusCode": 200, "body": "Ignorado"}

    logger.info(f"Procesando: s3://{bucket}/{key}")

    # Descargar y parsear unpacked_info.json
    try:
        response = s3.get_object(Bucket=bucket, Key=key)
        log_data = json.loads(response["Body"].read())
    except Exception as e:
        logger.error(f"No se pudo leer {key}: {str(e)}")
        return {"statusCode": 500, "body": "Error al leer metadata"}

    # Extraer archivos TIFF
    tiff_files = log_data.get("files", []) or log_data.get("tiff_files", [])
    if not tiff_files:
        logger.warning(f"No se encontraron archivos TIFF en {key}")
        return {"statusCode": 200, "body": "Sin archivos TIFF"}

    logger.info(f"Archivos TIFF encontrados: {len(tiff_files)}")

    # Procesar cada archivo
    processed = []
    failed = []

    for tif_key in tiff_files:
        try:
            logger.info(f"Procesando: {tif_key}")
            with tempfile.TemporaryDirectory() as tmpdir:
                input_path = os.path.join(tmpdir, "input.tif")
                output_path = os.path.join(tmpdir, "output_cog.tif")

                # Descargar TIFF
                s3.download_file(bucket, tif_key, input_path)
                logger.debug(f"Descargado: {input_path}")

                # Convertir a COG
                multiband_tiff_to_cog_stream(input_path, output_path)

                # Generar nuevo nombre basado en metadatos
                try:
                    date_prefix = get_date_from_metadata(tif_key)
                except Exception as e:
                    logger.warning(f"Usando nombre por defecto: {str(e)}")
                    date_prefix = "99999999"  # o podrías usar datetime.now().strftime("%Y%m%d")

                # Construir nuevo nombre: YYYYMMDD_composite_cog.tif
                base_name = os.path.basename(tif_key)
                if "composite" in base_name.lower():
                    new_name = f"{date_prefix}_composite_cog.tif"
                else:
                    # Para otros archivos, puedes usar otro esquema
                    clean_name = base_name.replace(".tif", "").replace(".tiff", "")
                    new_name = f"{date_prefix}_{clean_name}_cog.tif"

                cog_key = f"{os.path.dirname(tif_key)}/{new_name}"

                # Subir COG
                s3.upload_file(output_path, bucket, cog_key)
                logger.info(f"✅ COG subido: {cog_key}")

                processed.append(cog_key)

                # ✅ Eliminar archivos temporales
                try:
                    os.remove(input_path)
                    os.remove(output_path)
                except Exception as e:
                    logger.warning(f"No se pudo eliminar archivos temporales: {str(e)}")

        except Exception as e:
            logger.exception(f"❌ Fallo al procesar {tif_key}: {str(e)}")
            failed.append({"file": tif_key, "error": str(e)})

    # Resultado final
    result = {
        "status": "done",
        "input_log": key,
        "processed": len(processed),
        "failed": len(failed),
        "cog_files": processed,
        "errors": failed
    }

    logger.info(f"Resultado: {json.dumps(result)}")
    return {"statusCode": 200, "body": result}
