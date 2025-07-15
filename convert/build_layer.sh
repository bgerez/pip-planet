#!/bin/bash

set -e

echo "🚧 Construyendo capa rasterio + rio-cogeo para Lambda..."

# Construir imagen Docker
docker build -t lambda-rasterio-layer .

# Crear contenedor temporal para extraer el ZIP
docker create --name temp_container lambda-rasterio-layer

# Extraer el ZIP
docker cp temp_container:/layer/lambda_rasterio_layer.zip ./lambda_rasterio_layer.zip

# Eliminar contenedor temporal
docker rm temp_container

echo "✅ Capa construida correctamente:"
du -sh lambda_rasterio_layer.zip
