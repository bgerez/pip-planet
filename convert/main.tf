provider "aws" {
  region = "us-west-2"
}

#######################
# IAM Role for Lambda #
#######################

resource "aws_iam_role" "cog_lambda_role" {
  name = "lambda-cog-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
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
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject"
        ]
        Resource = [
          "arn:aws:s3:::ftp-download-procalculo-west2",
          "arn:aws:s3:::ftp-download-procalculo-west2/*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:*:*:*"
      }
    ]
  })
}

######################################
# Attach Policy to the Lambda Role   #
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
# CloudWatch Log Group  #
#########################

resource "aws_cloudwatch_log_group" "cog_lambda_log_group" {
  name              = "/aws/lambda/convert-to-cog"
  retention_in_days = 14

  lifecycle {
    ignore_changes = [retention_in_days]
  }
}

#########################
# SNS Topic: Notificaciones
#########################

resource "aws_sns_topic" "cog_summary" {
  name = "cog-summary-topic"
}

# (Opcional) Suscripción por email
# resource "aws_sns_topic_subscription" "cog_email_sub" {
#   topic_arn = aws_sns_topic.cog_summary.arn
#   protocol  = "email"
#   endpoint  = "bgerez@procalculo.com"
# }

#########################
# Lambda Function: COG  #
#########################

resource "aws_lambda_function" "cog_lambda" {
  function_name = "convert-to-cog"
  role          = aws_iam_role.cog_lambda_role.arn
  handler       = "lambda_function.lambda_handler"
  runtime       = "python3.11"
  timeout       = 900
  memory_size   = 3072  # Aumentado para manejar archivos grandes
  publish       = true

  filename         = "${path.module}/lambda_cog_package.zip"
  source_code_hash = filebase64sha256("${path.module}/lambda_cog_package.zip")

  layers = [
    aws_lambda_layer_version.rasterio_layer.arn
  ]

  ephemeral_storage {
    size = 10240  # 10 GB
  }

  environment {
    variables = {
      S3_BUCKET     = "ftp-download-procalculo-west2"
      SNS_TOPIC_ARN = aws_sns_topic.cog_summary.arn
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.cog_lambda_log_group.name
  }
}

#########################
# EventBridge Rule      #
# Dispara cuando se crea unpacked_info.json
#########################

resource "aws_cloudwatch_event_rule" "cog_trigger_rule" {
  name        = "invoke-cog-on-unpacked-info"
  description = "Trigger convert-to-cog when unpacked_info.json is created"
  event_pattern = jsonencode({
    source      = ["aws.s3"]
    "detail-type" = ["Object Created"]
    detail = {
      bucket = {
        name = ["ftp-download-procalculo-west2"]
      }
      object = {
        key = [{
          suffix = "unpacked_info.json"
        }]
      }
    }
  })
}

resource "aws_cloudwatch_event_target" "cog_lambda_target" {
  rule      = aws_cloudwatch_event_rule.cog_trigger_rule.name
  arn       = aws_lambda_function.cog_lambda.arn
}

resource "aws_lambda_permission" "allow_eventbridge_cog" {
  statement_id  = "AllowExecutionFromEventBridgeForCOG"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.cog_lambda.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.cog_trigger_rule.arn
}