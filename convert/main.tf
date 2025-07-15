provider "aws" {
  region = "us-west-2"
}

#######################
# IAM Role for Lambda #
#######################

resource "aws_iam_role" "cog_lambda_role" {
  name = "lambda-cog-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Effect = "Allow",
        Principal = {
          Service = "lambda.amazonaws.com"
        },
        Action = "sts:AssumeRole"
      }
    ]
  })
}

#########################
# IAM Policy for Lambda #
#########################

resource "aws_iam_policy" "cog_lambda_policy" {
  name = "lambda-cog-s3-policy"

  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Effect = "Allow",
        Action = [
          "s3:GetObject",
          "s3:PutObject"
        ],
        Resource = [
          "arn:aws:s3:::ftp-download-procalculo-west2",
          "arn:aws:s3:::ftp-download-procalculo-west2/*"
        ]
      },
      {
        Effect = "Allow",
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ],
        Resource = "arn:aws:logs:*:*:*"
      }
    ]
  })
}

######################################
# Attach Policy to the Lambda Role  #
######################################

resource "aws_iam_role_policy_attachment" "cog_lambda_policy_attach" {
  role       = aws_iam_role.cog_lambda_role.name
  policy_arn = aws_iam_policy.cog_lambda_policy.arn
}

####################
# Lambda Layer     #
####################

resource "aws_lambda_layer_version" "rasterio_layer" {
  filename            = "${path.module}/lambda_rasterio_layer.zip"
  layer_name          = "rasterio-cogeo-layer"
  compatible_runtimes = ["python3.11"]
  source_code_hash    = filebase64sha256("${path.module}/lambda_rasterio_layer.zip")
}

#########################
# Lambda Function: COG  #
#########################

resource "aws_lambda_function" "cog_lambda" {
  function_name = "convert-to-cog"
  role          = aws_iam_role.cog_lambda_role.arn
  handler       = "lambda_function.lambda_handler"
  runtime       = "python3.11"
  timeout       = 900
  memory_size   = 1024
  publish       = true

  filename         = "${path.module}/lambda_cog_package.zip"
  source_code_hash = filebase64sha256("${path.module}/lambda_cog_package.zip")

  layers = [
    aws_lambda_layer_version.rasterio_layer.arn
  ]

  ephemeral_storage {
    size = 10240
  }

  environment {
    variables = {
      S3_BUCKET = "ftp-download-procalculo-west2"
    }
  }
}

