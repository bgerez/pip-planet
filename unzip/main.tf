provider "aws" {
  region = "us-west-2"
}

##############################
# SNS Topic for Notifications
##############################

resource "aws_sns_topic" "unzip_notify" {
  name = "unzip-s3-files-notifications"
}

resource "aws_sns_topic_subscription" "email_subscription" {
  topic_arn = aws_sns_topic.unzip_notify.arn
  protocol  = "email"
  endpoint  = "bgerez@procalculo.com"
}

#######################
# IAM Role for Lambda #
#######################

resource "aws_iam_role" "unzip_lambda_role" {
  name = "lambda-unzip-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Effect = "Allow",
      Principal = {
        Service = "lambda.amazonaws.com"
      },
      Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_policy" "unzip_lambda_policy" {
  name = "lambda-unzip-policy"

  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Effect: "Allow",
        Action: [
          "s3:GetObject",
          "s3:PutObject",
          "s3:ListBucket",
          "s3:HeadObject"
        ],
        Resource: [
          "arn:aws:s3:::ftp-download-procalculo-west2",
          "arn:aws:s3:::ftp-download-procalculo-west2/*"
        ]
      },
      {
        Effect: "Allow",
        Action: [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ],
        Resource: "arn:aws:logs:*:*:*"
      },
      {
        Effect: "Allow",
        Action: "sns:Publish",
        Resource: aws_sns_topic.unzip_notify.arn
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "unzip_lambda_attach" {
  role       = aws_iam_role.unzip_lambda_role.name
  policy_arn = aws_iam_policy.unzip_lambda_policy.arn
}

resource "aws_s3_bucket_notification" "trigger_unzip_lambda" {
  bucket = "ftp-download-procalculo-west2"

  lambda_function {
    lambda_function_arn = aws_lambda_function.unzip_lambda.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = ""                   # Aplica a todo
    filter_suffix       = ".zip"               # Solo archivos .zip
  }

  depends_on = [
    aws_lambda_permission.allow_s3_to_invoke
  ]
}

resource "aws_lambda_permission" "allow_s3_to_invoke" {
  statement_id  = "AllowExecutionFromS3"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.unzip_lambda.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = "arn:aws:s3:::ftp-download-procalculo-west2"
}


#########################
# Lambda unzip function #
#########################

resource "aws_lambda_function" "unzip_lambda" {
  function_name = "unzip-s3-files"
  role          = aws_iam_role.unzip_lambda_role.arn
  handler       = "lambda_function.lambda_handler"
  runtime       = "python3.11"
  timeout       = 900
  memory_size   = 1024

  filename         = "${path.module}/lambda_package.zip"
  source_code_hash = filebase64sha256("${path.module}/lambda_package.zip")

  environment {
    variables = {
      SNS_TOPIC_ARN = aws_sns_topic.unzip_notify.arn
    }
  }
}
