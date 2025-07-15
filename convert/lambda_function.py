import os
import json
import boto3
import tempfile
import rasterio
from rio_cogeo.cogeo import cog_translate

s3 = boto3.client("s3")

def multiband_tiff_to_cog_stream(input_path: str, output_path: str, compress: str = "ZSTD"):
    """
    Convierte un GeoTIFF multibanda a Cloud Optimized GeoTIFF (COG) usando procesamiento por bloques.
    Ideal para AWS Lambda: evita cargar todo el array en memoria.
    """
    print(f"[DEBUG] Streaming conversion: {input_path} → {output_path}, compress={compress}")

    cog_profile = {
        "driver": "GTiff",
        "tiled": True,
        "blockxsize": 1024,
        "blockysize": 1024,
        "compress": compress,
        "NUM_THREADS": "ALL_CPUS",
    }

    with rasterio.open(input_path, "r") as src:
        print(f"[DEBUG] Archivo abierto correctamente")
        cog_translate(
            src,
            output_path,
            dst_kwargs=cog_profile,
            overview_resampling="average"
        )

    print(f"[DEBUG] ✅ COG generado exitosamente → {output_path}")


def lambda_handler(event, context):
    bucket = os.environ.get("S3_BUCKET")
    log_key = event.get("log_key")

    print(f"[DEBUG] Bucket: {bucket}")
    print(f"[DEBUG] Log Key: {log_key}")

    # Descargar log.json desde S3
    response = s3.get_object(Bucket=bucket, Key=log_key)
    log_data = json.loads(response["Body"].read())

    tiff_files = log_data.get("files", []) or log_data.get("tif_files", [])
    print(f"[DEBUG] Archivos TIFF para procesar: {tiff_files}")

    for tif_key in tiff_files:
        print(f"[DEBUG] Procesando archivo: {tif_key}")

        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = os.path.join(tmpdir, "input.tif")
            output_path = os.path.join(tmpdir, "output_cog.tif")

            try:
                # Descargar TIFF desde S3
                s3.download_file(bucket, tif_key, input_path)
                print(f"[DEBUG] Archivo descargado a: {input_path}")

                # Convertir a COG
                multiband_tiff_to_cog_stream(input_path, output_path)

                # Subir el COG a S3
                cog_key = tif_key.replace(".tif", "_COG.tif")
                s3.upload_file(output_path, bucket, cog_key)
                print(f"[DEBUG] ✅ COG subido exitosamente: {cog_key}")

            except Exception as e:
                print(f"[ERROR] ❌ Error durante el procesamiento de {tif_key}:\n{e}")

    return {"status": "done"}
