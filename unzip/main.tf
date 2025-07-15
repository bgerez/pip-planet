provider "aws" {
  region = "us-west-2"
}

resource "aws_iam_role" "unzip_lambda_role" {
  name = "lambda-unzip-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_policy" "unzip_lambda_policy" {
  name = "lambda-unzip-s3-policy"

  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Effect = "Allow",
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:ListBucket"
        ],
        Resource = [
          "arn:aws:s3:::ftp-download-procalculo-west2",
          "arn:aws:s3:::ftp-download-procalculo-west2/*"
        ]
      },
      {
        Effect = "Allow",
        Action = "logs:*",
        Resource = "*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "unzip_lambda_policy_attach" {
  role       = aws_iam_role.unzip_lambda_role.name
  policy_arn = aws_iam_policy.unzip_lambda_policy.arn
}

resource "aws_lambda_function" "unzip_lambda" {
  function_name = "unzip-s3-files"
  role          = aws_iam_role.unzip_lambda_role.arn
  handler       = "lambda_function.lambda_handler"
  runtime       = "python3.11"
  timeout       = 300
  memory_size   = 1024

  ephemeral_storage {
    size = 10240 # 1 GB para almacenar archivos temporalmente
  }

  filename         = "${path.module}/lambda_package.zip"
  source_code_hash = filebase64sha256("${path.module}/lambda_package.zip")
}
