provider "aws" {
  region = "us-west-2"
}

# --- VPC y Subnets ---
resource "aws_vpc" "lambda_vpc" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = "lambda-vpc"
  }
}

resource "aws_subnet" "lambda_subnet_a" {
  vpc_id                  = aws_vpc.lambda_vpc.id
  cidr_block              = "10.0.1.0/24"
  availability_zone       = "us-west-2a"
  map_public_ip_on_launch = false

  tags = {
    Name = "lambda-subnet-a"
  }
}

resource "aws_subnet" "lambda_subnet_b" {
  vpc_id                  = aws_vpc.lambda_vpc.id
  cidr_block              = "10.0.2.0/24"
  availability_zone       = "us-west-2b"
  map_public_ip_on_launch = false

  tags = {
    Name = "lambda-subnet-b"
  }
}

# --- Tabla de rutas privada (opcional pero recomendada para claridad) ---
resource "aws_route_table" "private" {
  vpc_id = aws_vpc.lambda_vpc.id

  tags = {
    Name = "private-rt"
  }
}

resource "aws_route_table_association" "private_a" {
  subnet_id      = aws_subnet.lambda_subnet_a.id
  route_table_id = aws_route_table.private.id
}

resource "aws_route_table_association" "private_b" {
  subnet_id      = aws_subnet.lambda_subnet_b.id
  route_table_id = aws_route_table.private.id
}

# --- VPC Endpoint para S3 (Gateway) ---
resource "aws_vpc_endpoint" "s3" {
  vpc_id       = aws_vpc.lambda_vpc.id
  service_name = "com.amazonaws.us-west-2.s3"

  # Gateway endpoints usan route_table_ids, no subnet_ids
  route_table_ids = [
    aws_vpc.lambda_vpc.default_route_table_id,
    aws_route_table.private.id
  ]

  tags = {
    Name = "vpce-s3"
  }
}

# --- EFS File System ---
resource "aws_efs_file_system" "lambda_efs" {
  creation_token = "lambda-efs-unzip"

  tags = {
    Name = "lambda-efs"
  }
}

# --- EFS Mount Targets ---
resource "aws_efs_mount_target" "efs_mt_a" {
  file_system_id  = aws_efs_file_system.lambda_efs.id
  subnet_id       = aws_subnet.lambda_subnet_a.id
  security_groups = [aws_security_group.lambda_sg.id]
}

resource "aws_efs_mount_target" "efs_mt_b" {
  file_system_id  = aws_efs_file_system.lambda_efs.id
  subnet_id       = aws_subnet.lambda_subnet_b.id
  security_groups = [aws_security_group.lambda_sg.id]
}

# --- Security Group para Lambda y EFS ---
resource "aws_security_group" "lambda_sg" {
  name        = "lambda-efs-sg"
  description = "Allow NFS traffic from Lambda"
  vpc_id      = aws_vpc.lambda_vpc.id

  ingress {
    from_port   = 2049
    to_port     = 2049
    protocol    = "tcp"
    cidr_blocks = ["10.0.0.0/16"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "lambda-sg"
  }
}

# --- EFS Access Point ---
resource "aws_efs_access_point" "lambda_ap" {
  file_system_id = aws_efs_file_system.lambda_efs.id

  posix_user {
    gid = 1000
    uid = 1000
  }

  root_directory {
    path = "/lambda"
    creation_info {
      owner_gid   = 1000
      owner_uid   = 1000
      permissions = "750"
    }
  }
}

# --- SQS: Cola DLQ (Dead Letter Queue) ---
resource "aws_sqs_queue" "unzip_event_dlq" {
  name                         = "unzip-s3-event-dlq.fifo"
  fifo_queue                  = true
  content_based_deduplication = true
  delay_seconds               = 0
}

# --- SQS: Cola principal ---
resource "aws_sqs_queue" "unzip_event_queue" {
  name                              = "unzip-s3-event-queue.fifo"
  fifo_queue                        = true
  content_based_deduplication       = true
  visibility_timeout_seconds        = 900
  message_retention_seconds         = 86400

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.unzip_event_dlq.arn
    maxReceiveCount     = 3
  })
}

# --- IAM Role: Lambda Execution Role ---
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
        Action = ["s3:GetObject", "s3:PutObject", "s3:HeadObject", "s3:ListBucket"]
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
        Action = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
        Effect = "Allow"
        Resource = aws_sqs_queue.unzip_event_queue.arn
      },
      {
        Action = ["elasticfilesystem:ClientMount", "elasticfilesystem:ClientWrite", "elasticfilesystem:ClientRootAccess"]
        Effect = "Allow"
        Resource = aws_efs_file_system.lambda_efs.arn
      },
      {
        Action = [
          "ec2:CreateNetworkInterface",
          "ec2:DescribeNetworkInterfaces",
          "ec2:DeleteNetworkInterface"
        ]
        Effect = "Allow"
        Resource = "*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "custom_permissions_attach" {
  role       = aws_iam_role.lambda_exec_role.name
  policy_arn = aws_iam_policy.custom_lambda_permissions.arn
}

# --- SNS Topic ---
resource "aws_sns_topic" "unzip_summary" {
  name = "unzip-summary-topic"
}

# --- CloudWatch Logs ---
resource "aws_cloudwatch_log_group" "lambda_log_group" {
  name              = "/aws/lambda/unzip-s3-files"
  retention_in_days = 14

  lifecycle {
    ignore_changes = [retention_in_days]
  }
}

# --- EventBridge Rule ---
resource "aws_cloudwatch_event_rule" "s3_put_zip_rule" {
  name        = "invoke-sqs-on-zip-upload"
  description = "Send S3 .zip events to SQS for reliable processing"

  event_pattern = jsonencode({
    source        = ["aws.s3"]
    "detail-type" = ["Object Created"]
    detail = {
      bucket = { name = ["ftp-download-procalculo-west2"] }
      object = { key = [{ suffix = ".zip" }] }
      reason = ["PutObject", "CompleteMultipartUpload", "CopyObject"]
    }
  })
}

# --- IAM Role: EventBridge → SQS ---
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

# --- EventBridge Target: Envía eventos a SQS ---
resource "aws_cloudwatch_event_target" "sqs_target" {
  rule      = aws_cloudwatch_event_rule.s3_put_zip_rule.name
  target_id = "sqs-queue-target"
  arn       = aws_sqs_queue.unzip_event_queue.arn
  role_arn  = aws_iam_role.eventbridge_to_sqs_role.arn

  sqs_target {
    message_group_id = "unzip-s3-files"
  }
}

# --- Lambda Function ---
resource "aws_lambda_function" "unzip_s3_files" {
  function_name = "unzip-s3-files"
  handler       = "lambda_function.lambda_handler"
  runtime       = "python3.12"
  role          = aws_iam_role.lambda_exec_role.arn
  timeout       = 900
  memory_size   = 10240

  ephemeral_storage {
    size = 10240
  }

  filename = "lambda_package.zip"

  environment {
    variables = {
      SNS_TOPIC_ARN              = aws_sns_topic.unzip_summary.arn
      MAX_TOTAL_UNZIPPED_SIZE_MB = 20480
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.lambda_log_group.name
  }

  vpc_config {
    subnet_ids         = [aws_subnet.lambda_subnet_a.id, aws_subnet.lambda_subnet_b.id]
    security_group_ids = [aws_security_group.lambda_sg.id]
  }

  file_system_config {
    arn              = aws_efs_access_point.lambda_ap.arn
    local_mount_path = "/mnt/efs"
  }
}

# --- Lambda SQS Trigger ---
resource "aws_lambda_event_source_mapping" "sqs_mapping" {
  event_source_arn = aws_sqs_queue.unzip_event_queue.arn
  function_name    = aws_lambda_function.unzip_s3_files.arn
  batch_size       = 1
  enabled          = true
}
resource "aws_vpc_endpoint" "sns" {
  vpc_id            = aws_vpc.lambda_vpc.id
  service_name      = "com.amazonaws.us-west-2.sns"
  vpc_endpoint_type = "Interface"
  subnet_ids        = [aws_subnet.lambda_subnet_a.id, aws_subnet.lambda_subnet_b.id]
  security_group_ids = [aws_security_group.lambda_sg.id]
  private_dns_enabled = true

  tags = {
    Name = "vpce-sns"
    "Centro de Costos" = "Primario"
  }
}
