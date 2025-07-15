FROM public.ecr.aws/lambda/python:3.11

# Herramientas necesarias para compilar paquetes científicos
RUN yum install -y \
    gcc gcc-c++ make \
    gdal gdal-devel \
    proj proj-devel \
    geos geos-devel \
    zip \
    && yum clean all

# Crear carpeta destino
WORKDIR /opt

# Copiar requirements
COPY requirements.txt /tmp/requirements.txt

# Instalar dependencias en /opt/python
RUN pip install --upgrade pip && \
    pip install --no-cache-dir --target=python -r /tmp/requirements.txt

# 🔥 Limpieza para reducir tamaño de capa
RUN find python -type d -name "tests" -exec rm -rf {} + && \
    find python -type d -name "__pycache__" -exec rm -rf {} + && \
    find python -name "*.pyc" -delete && \
    find python -name "*.dist-info" -exec rm -rf {} + && \
    mkdir -p /layer && \
    cd /opt && zip -r9 /layer/lambda_rasterio_layer.zip python
