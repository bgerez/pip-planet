provider "aws" {
  region = "us-west-2"
}

# --- SQS: Cola DLQ (Dead Letter Queue) ---
resource "aws_sqs_queue" "unzip_event_dlq" {
  name                      = "unzip-s3-event-dlq.fifo"
  fifo_queue                = true
  content_based_deduplication = true
  delay_seconds             = 0
}

# --- SQS: Cola principal (recibe eventos de S3 vía EventBridge) ---
resource "aws_sqs_queue" "unzip_event_queue" {
  name                              = "unzip-s3-event-queue.fifo"
  fifo_queue                        = true
  content_based_deduplication       = true
  visibility_timeout_seconds        = 900  # Debe ser >= timeout de Lambda
  message_retention_seconds         = 86400 # 24 horas

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.unzip_event_dlq.arn
    maxReceiveCount     = 3  # ✅ Obligatorio para FIFO
  })
}

# --- IAM Role: Para que Lambda asuma permisos ---
resource "aws_iam_role" "lambda_exec_role" {
  name = "lambda_exec_role_unzip"

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

# --- Políticas IAM para Lambda ---
resource "aws_iam_role_policy_attachment" "lambda_basic" {
  role       = aws_iam_role.lambda_exec_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_policy" "custom_lambda_permissions" {
  name = "lambda-s3-sns-sqs-policy"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:HeadObject",
          "s3:ListBucket"
        ]
        Effect = "Allow"
        Resource = [
          "arn:aws:s3:::ftp-download-procalculo-west2",
          "arn:aws:s3:::ftp-download-procalculo-west2/*"
        ]
      },
      {
        Action = "sns:Publish"
        Effect = "Allow"
        Resource = aws_sns_topic.unzip_summary.arn
      },
      {
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes"
        ]
        Effect = "Allow"
        Resource = aws_sqs_queue.unzip_event_queue.arn
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "custom_permissions_attach" {
  role       = aws_iam_role.lambda_exec_role.name
  policy_arn = aws_iam_policy.custom_lambda_permissions.arn
}

# --- SNS Topic: Notificaciones de éxito/error ---
resource "aws_sns_topic" "unzip_summary" {
  name = "unzip-summary-topic"
}

# --- CloudWatch Log Group: Para logs de Lambda ---
resource "aws_cloudwatch_log_group" "lambda_log_group" {
  name              = "/aws/lambda/unzip-s3-files"
  retention_in_days = 14

  # Evita fallo si ya existe
  lifecycle {
    ignore_changes = [retention_in_days]
  }
}

# --- Regla de EventBridge (CloudWatch Event Rule): Detecta .zip en S3 ---
resource "aws_cloudwatch_event_rule" "s3_put_zip_rule" {
  name        = "invoke-sqs-on-zip-upload"
  description = "Send S3 .zip events to SQS for reliable processing"
  event_pattern = jsonencode({
    source      = ["aws.s3"]
    "detail-type" = ["Object Created"]
    detail = {
      bucket = {
        name = ["ftp-download-procalculo-west2"]
      }
      object = {
        key = [{
          suffix = ".zip"
        }]
      }
      reason = ["PutObject", "CompleteMultipartUpload", "CopyObject"]
    }
  })
}

# --- Target: Enruta el evento de EventBridge a SQS ---
resource "aws_cloudwatch_event_target" "sqs_target" {
  rule      = aws_cloudwatch_event_rule.s3_put_zip_rule.name
  target_id = "sqs-queue-target"
  arn       = aws_sqs_queue.unzip_event_queue.arn
  role_arn  = aws_iam_role.eventbridge_to_sqs_role.arn

  # ✅ Obligatorio para colas FIFO
  sqs_target {
    message_group_id = "unzip-s3-files"
  }
}

# --- IAM Role: Permite que EventBridge envíe a SQS ---
resource "aws_iam_role" "eventbridge_to_sqs_role" {
  name = "eventbridge-to-sqs-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "events.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })
}

resource "aws_iam_role_policy" "eventbridge_sqs_policy" {
  name = "eventbridge-sqs-policy"
  role = aws_iam_role.eventbridge_to_sqs_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = "sqs:SendMessage"
        Resource = aws_sqs_queue.unzip_event_queue.arn
      }
    ]
  })
}

# --- Lambda Function: Descomprime el ZIP ---
resource "aws_lambda_function" "unzip_s3_files" {
  function_name = "unzip-s3-files"
  handler       = "lambda_function.lambda_handler"
  runtime       = "python3.12"
  role          = aws_iam_role.lambda_exec_role.arn
  timeout       = 900
  memory_size   = 10240  # 10 GB

  ephemeral_storage {
    size = 10240  # 10 GB
  }

  # ✅ Archivo ZIP que sí existe
  filename = "lambda_package.zip"

  environment {
    variables = {
      SNS_TOPIC_ARN               = aws_sns_topic.unzip_summary.arn
      MAX_TOTAL_UNZIPPED_SIZE_MB  = 20480
    }
  }

  # ✅ logging_config completo
  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.lambda_log_group.name
  }
}

# --- Event Source Mapping: SQS → Lambda (con reintentos automáticos) ---
resource "aws_lambda_event_source_mapping" "sqs_mapping" {
  event_source_arn  = aws_sqs_queue.unzip_event_queue.arn
  function_name     = aws_lambda_function.unzip_s3_files.arn
  batch_size        = 1
  enabled           = true
#  maximum_batching_window_in_seconds = 5
}