# versions.tf
terraform {
  required_version = ">= 1.4"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.4.0"  # Usa la última estable de la v6
    }
  }
}
