# Names here must match ocrbench/config.py (Config.bucket, endpoint_name, model_name, ...).
locals {
  p             = var.name_prefix
  bucket        = "${local.p}-artifacts"
  endpoint_name = "${local.p}-ocr"
  model_name    = "${local.p}-ocr-model"
  registry      = "${var.account_id}.dkr.ecr.${var.region}.amazonaws.com"
}

# ---------------------------------------------------------------------------
# Network: existing VPC (looked up by id or Name tag) or a small dedicated one
# ---------------------------------------------------------------------------
data "aws_vpc" "existing" {
  count = var.network_mode == "existing" ? 1 : 0
  id    = var.existing_vpc_id
  dynamic "filter" {
    for_each = var.existing_vpc_id == null ? [1] : []
    content {
      name   = "tag:Name"
      values = [var.existing_vpc_name_tag]
    }
  }
}

data "aws_subnets" "existing" {
  count = var.network_mode == "existing" && length(var.existing_subnet_ids) == 0 ? 1 : 0
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.existing[0].id]
  }
  filter {
    name   = "tag:Name"
    values = [var.existing_subnet_name_tag_glob]
  }
}

module "network" {
  source = "./network"
  count  = var.network_mode == "create" ? 1 : 0
  name   = local.p
  cidr   = var.create_cidr
}

locals {
  vpc_id = var.network_mode == "create" ? module.network[0].vpc_id : data.aws_vpc.existing[0].id
  subnet_ids = (var.network_mode == "create" ? module.network[0].private_subnet_ids
  : length(var.existing_subnet_ids) > 0 ? var.existing_subnet_ids : sort(data.aws_subnets.existing[0].ids))
}

resource "aws_security_group" "runner" {
  name        = "${local.p}-runner"
  description = "ocr-bench runner: no ingress, all egress"
  vpc_id      = local.vpc_id
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# ---------------------------------------------------------------------------
# Artifacts: one bucket (weights, docs, config, results), two image repos
# ---------------------------------------------------------------------------
resource "aws_s3_bucket" "artifacts" {
  bucket        = local.bucket
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    id     = "expire"
    status = "Enabled"
    filter {}
    expiration {
      days = 60
    }
  }
}

resource "aws_s3_bucket_policy" "tls_only" {
  bucket = aws_s3_bucket.artifacts.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.artifacts.arn, "${aws_s3_bucket.artifacts.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
  depends_on = [aws_s3_bucket_public_access_block.artifacts]
}

resource "aws_ecr_repository" "repo" {
  for_each             = toset(["vllm", "bench"])
  name                 = "${local.p}-${each.key}"
  image_tag_mutability = "MUTABLE"
  force_delete         = true
}

# ---------------------------------------------------------------------------
# SageMaker model (endpoint itself is created by `ocrbench endpoint up`)
# ---------------------------------------------------------------------------
resource "aws_iam_role" "sagemaker" {
  name_prefix          = "${substr(local.p, 0, 20)}-sm-"
  permissions_boundary = var.permissions_boundary_arn
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "sagemaker.amazonaws.com" } }]
  })
}

resource "aws_iam_role_policy" "sagemaker" {
  role = aws_iam_role.sagemaker.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:GetObject", "s3:ListBucket"],
      Resource = [aws_s3_bucket.artifacts.arn, "${aws_s3_bucket.artifacts.arn}/${var.model_s3_prefix}/*"] },
      { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
      { Effect = "Allow", Action = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"],
      Resource = aws_ecr_repository.repo["vllm"].arn },
      { Effect = "Allow", Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"],
      Resource = "arn:aws:logs:${var.region}:${var.account_id}:log-group:/aws/sagemaker/*" },
      { Effect = "Allow", Action = ["cloudwatch:PutMetricData"], Resource = "*" },
    ]
  })
}

resource "aws_sagemaker_model" "ocr" {
  count                    = var.create_ocr_model ? 1 : 0
  name                     = local.model_name
  execution_role_arn       = aws_iam_role.sagemaker.arn
  enable_network_isolation = var.sagemaker_network_isolation
  primary_container {
    image = "${aws_ecr_repository.repo["vllm"].repository_url}:${var.ocr_image_tag}"
    model_data_source {
      s3_data_source {
        s3_uri           = "s3://${local.bucket}/${var.model_s3_prefix}/"
        s3_data_type     = "S3Prefix"
        compression_type = "None"
      }
    }
    environment = { MAX_NUM_SEQS = var.max_num_seqs }
  }
  depends_on = [aws_iam_role_policy.sagemaker]
}

# ---------------------------------------------------------------------------
# Bench runner: Fargate task inside the VPC
# ---------------------------------------------------------------------------
resource "aws_ecs_cluster" "bench" {
  count = var.ecs_cluster == "create" ? 1 : 0
  name  = local.p
}

locals {
  cluster_name = var.ecs_cluster == "create" ? aws_ecs_cluster.bench[0].name : var.ecs_cluster
}

resource "aws_cloudwatch_log_group" "bench" {
  name              = "/ecs/${local.p}-bench"
  retention_in_days = 14
}

data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "exec" {
  name_prefix          = "${substr(local.p, 0, 20)}-exec-"
  permissions_boundary = var.permissions_boundary_arn
  assume_role_policy   = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy" "exec" {
  role = aws_iam_role.exec.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
      { Effect = "Allow", Action = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"],
      Resource = aws_ecr_repository.repo["bench"].arn },
      { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.bench.arn}:*" },
    ]
  })
}

resource "aws_iam_role" "task" {
  name_prefix          = "${substr(local.p, 0, 20)}-task-"
  permissions_boundary = var.permissions_boundary_arn
  assume_role_policy   = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy" "task" {
  role = aws_iam_role.task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      [
        { Effect = "Allow", Action = ["s3:ListBucket"], Resource = aws_s3_bucket.artifacts.arn },
        { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject"], Resource = "${aws_s3_bucket.artifacts.arn}/runs/*" },
        { Effect = "Allow", Action = ["sagemaker:InvokeEndpoint", "sagemaker:DescribeEndpoint"],
        Resource = "arn:aws:sagemaker:${var.region}:${var.account_id}:endpoint/${local.endpoint_name}" },
      ],
      length(var.kms_sign_key_arns) > 0 ? [{ Effect = "Allow", Action = ["kms:Sign"], Resource = var.kms_sign_key_arns }] : [],
      length(var.bedrock_model_ids) > 0 ? [{
        Effect = "Allow", Action = ["bedrock:InvokeModel", "bedrock:Converse"],
        Resource = flatten([for m in var.bedrock_model_ids : [
          "arn:aws:bedrock:*::foundation-model/${m}",
          "arn:aws:bedrock:*:${var.account_id}:inference-profile/${m}",
        ]])
      }] : [],
    )
  })
}

resource "aws_ecs_task_definition" "bench" {
  family                   = "${local.p}-bench"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.runner_cpu)
  memory                   = tostring(var.runner_memory)
  execution_role_arn       = aws_iam_role.exec.arn
  task_role_arn            = aws_iam_role.task.arn
  runtime_platform {
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }
  container_definitions = jsonencode([{
    name       = "bench"
    image      = "${aws_ecr_repository.repo["bench"].repository_url}:${var.bench_image_tag}"
    essential  = true
    entryPoint = ["python", "-m", "ocrbench.cli"]
    command    = ["--help"]
    environment = [
      { name = "OCRBENCH_BUCKET", value = local.bucket },
      { name = "AWS_REGION", value = var.region },
      { name = "PYTHONUNBUFFERED", value = "1" },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.bench.name
        awslogs-region        = var.region
        awslogs-stream-prefix = "bench"
      }
    }
  }])
}
