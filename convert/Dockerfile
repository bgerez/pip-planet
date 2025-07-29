# Usa la imagen base oficial de AWS Lambda para Python 3.11
FROM public.ecr.aws/lambda/python:3.11

# Instalar dependencias del sistema, incluyendo `zip`
RUN yum update -y && \
    yum install -y \
        gcc gcc-c++ make \
        git \
        wget \
        unzip \
        tar \
        gzip \
        bzip2 \
        ca-certificates \
        curl \
        zip \  
    && yum clean all

# Directorio de trabajo
WORKDIR /var/task

# Copiar requirements
COPY requirements.txt ./

# Instalar dependencias en /opt/python
RUN mkdir -p /opt/python && \
    pip install --upgrade pip && \
    pip install --no-cache-dir --target /opt/python -r requirements.txt

# 🔧 Limpieza agresiva para reducir tamaño
RUN find /opt/python -type d -name "tests" -exec rm -rf {} + && \
    find /opt/python -type d -name "__pycache__" -exec rm -rf {} + && \
    find /opt/python -name "*.pyc" -delete && \
    find /opt/python -name "*.dist-info" -exec rm -rf {} +

# 📦 Empaquetar como capa Lambda
RUN cd /opt && zip -r9 /var/task/lambda_rasterio_layer.zip python