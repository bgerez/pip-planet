# 🚀 Unzip & COG Conversion Lambda Deployment with Terraform + Docker

Este proyecto despliega dos funciones AWS Lambda con Terraform:

1. `unzip_lambda`: descomprime archivos `.zip` en un bucket S3.
2. `convert_lambda`: convierte imágenes TIFF multibanda a formato Cloud Optimized GeoTIFF (COG) usando Rasterio y rio-cogeo.

---

## 🧰 Requisitos

-

---

## 💠 Instalación de herramientas

### 1. Terraform

```bash
sudo apt update && sudo apt install -y gnupg software-properties-common curl
curl -fsSL https://apt.releases.hashicorp.com/gpg | sudo gpg --dearmor -o /usr/share/keyrings/hashicorp-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/hashicorp.list
sudo apt update
sudo apt install terraform
terraform -version
```

### 2. Docker

```bash
sudo apt install docker.io
sudo systemctl enable docker
sudo systemctl start docker
sudo usermod -aG docker $USER
newgrp docker  # Requiere cerrar sesión o reiniciar terminal
docker --version
```

---

## 📦 Construcción de la Lambda Layer con Docker (para `convert`)

### Ubicación: `convert/`

```bash
cd convert/
chmod +x build_layer.sh
./build_layer.sh
```

Este script genera el archivo `lambda_rasterio_layer.zip` compatible con Lambda, con Rasterio, NumPy y rio-cogeo preinstalados.

---

## 🧳 Empaquetado de funciones Lambda

### 1. Unzip Function (carpeta `unzip/`)

```bash
cd unzip/
zip lambda_package.zip lambda_function.py
```

### 2. Convert Function (carpeta `convert/`)

```bash
cd convert/
zip lambda_cog_package.zip lambda_function.py
```

---

## 🚀 Despliegue con Terraform

### Desplegar `unzip_lambda`

```bash
cd unzip/
terraform init
terraform apply
```

### Desplegar `convert_lambda`

```bash
cd convert/
terraform init
terraform apply
```

---

## 🧠 Descripción del Proyecto

Este proyecto tiene como objetivo automatizar un flujo de trabajo geoespacial:

1. **Descomprimir archivos .zip** con imágenes satelitales TIFF.
2. **Convertir TIFF multibanda a COG** usando compresión ZSTD.
3. El proceso está desacoplado en dos funciones Lambda para facilitar la trazabilidad, mantenimiento y escalabilidad.
4. Los artefactos son generados localmente y desplegados con Terraform.

---

## 📁 Estructura de carpetas del proyecto

```
Deploy-TF/
├── .git/
├── .gitattributes
├── .gitignore
├── README.md
├── convert/
│   ├── build_layer.sh
│   ├── Dockerfile
│   ├── event.json
│   ├── lambda_cog_package.zip
│   ├── lambda_function.py
│   ├── lambda_rasterio_layer.zip
│   ├── main.tf
│   ├── requirements.txt
│   ├── .terraform/
│   ├── .terraform.lock.hcl
│   ├── terraform.tfstate
│   └── terraform.tfstate.backup
├── convert.zip
├── unzip/
│   ├── lambda_function.py
│   ├── lambda_package.zip
│   ├── main.tf
│   ├── .terraform/
│   ├── .terraform.lock.hcl
│   ├── terraform.tfstate
│   └── terraform.tfstate.backup
```

---

## 🧩 Notas finales

- El archivo `lambda_rasterio_layer.zip` puede ser pesado. Se recomienda usar Git LFS para manejarlo si se va a versionar.
- Los archivos `.terraform/` están ignorados por Git. No deberían subirse.
- Asegúrate de tener permisos suficientes en tu cuenta de AWS para crear funciones Lambda, roles IAM, y políticas necesarias.

---

## 🧑‍💻 Autor

**Brayan Gerez**\
Ingeniero de Sistemas | DevOps | AWS Specialist\
Repositorio: [github.com/bgerez/pip-planet](https://github.com/bgerez/pip-planet)

