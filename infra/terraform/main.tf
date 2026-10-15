# Names here must match ocrbench/config.py (Config.bucket, endpoint_name(), model_name(), ...).
locals {
  p        = var.name_prefix
  bucket   = "${local.p}-artifacts"
  registry = "${var.account_id}.dkr.ecr.${var.region}.amazonaws.com"
  # one SageMaker endpoint per self-hosted backend: <prefix>-<name>
  endpoint_arns = [for k in keys(var.selfhosted) : "arn:aws:sagemaker:${var.region}:${var.account_id}:endpoint/${local.p}-${k}"]
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
  # Run copies and docs expire (retention.runs_days; 0 = never); model weights are kept for as long as the
  # stack exists.
  dynamic "rule" {
    for_each = var.retention_runs_days > 0 ? [var.retention_runs_days] : []
    content {
      id     = "expire-runs"
      status = "Enabled"
      filter {
        prefix = "runs/"
      }
      expiration {
        days = rule.value
      }
    }
  }
  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 2
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
  image_tag_mutability = "MUTABLE" # tags move on rebuild; deployments reference digests
  force_delete         = true
  image_scanning_configuration {
    scan_on_push = true
  }
}

# Rebuilds move the tags, leaving the previous images untagged; deployments pin digests and every
# rebuild re-applies, so untagged images can go (retention.ecr_untagged_days). Mirrors of source images
# (mirror-<digest>, vllm repo) are big: keep the newest retention.ecr_keep_mirrors (an expired one is simply
# mirrored again by the next `just images`).
resource "aws_ecr_lifecycle_policy" "repo" {
  for_each   = aws_ecr_repository.repo
  repository = each.value.name
  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "keep the ${var.retention_ecr_keep_mirrors == 2 ? "two" : var.retention_ecr_keep_mirrors} newest source-image mirrors" # wording of the original policy
        selection    = { tagStatus = "tagged", tagPrefixList = ["mirror-"], countType = "imageCountMoreThan", countNumber = var.retention_ecr_keep_mirrors }
        action       = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "expire untagged images after ${var.retention_ecr_untagged_days} days"
        selection    = { tagStatus = "untagged", countType = "sinceImagePushed", countUnit = "days", countNumber = var.retention_ecr_untagged_days }
        action       = { type = "expire" }
      },
    ]
  })
}

# ---------------------------------------------------------------------------
# SageMaker models, one per self-hosted backend (endpoints are created by `ocrbench endpoint up <name>`)
# ---------------------------------------------------------------------------
resource "aws_iam_role" "sagemaker" {
  name_prefix          = "${substr(local.p, 0, 20)}-sm-"
  permissions_boundary = var.permissions_boundary_arn
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "sagemaker.amazonaws.com" } }]
  })
}

# Endpoint logs. SageMaker would create these itself, without retention; Terraform owns them so they
# expire (and the role needs no logs:CreateLogGroup). A stack whose endpoints ran before this existed
# already has the groups: import each one once, e.g.
#   just tf import 'aws_cloudwatch_log_group.endpoint["<name>"]' /aws/sagemaker/Endpoints/<name_prefix>-<name>
resource "aws_cloudwatch_log_group" "endpoint" {
  for_each          = var.selfhosted
  name              = "/aws/sagemaker/Endpoints/${local.p}-${each.key}"
  retention_in_days = var.retention_log_days
}

resource "aws_iam_role_policy" "sagemaker" {
  role = aws_iam_role.sagemaker.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:GetObject"], Resource = "${aws_s3_bucket.artifacts.arn}/models/*" },
      { Effect = "Allow", Action = ["s3:ListBucket"], Resource = aws_s3_bucket.artifacts.arn,
      Condition = { StringLike = { "s3:prefix" = ["models/*"] } } },
      { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
      { Effect = "Allow", Action = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"],
      Resource = aws_ecr_repository.repo["vllm"].arn },
      { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"],
      Resource = "arn:aws:logs:${var.region}:${var.account_id}:log-group:/aws/sagemaker/*" },
      { Effect = "Allow", Action = ["cloudwatch:PutMetricData"], Resource = "*" },
    ]
  })
}

resource "aws_sagemaker_model" "selfhosted" {
  for_each                 = { for k, v in var.selfhosted : k => v if contains(var.create_selfhosted_models, k) }
  name                     = "${local.p}-${each.key}-model"
  execution_role_arn       = aws_iam_role.sagemaker.arn
  enable_network_isolation = each.value.network_isolation
  primary_container {
    image = each.value.image_ref != "" ? each.value.image_ref : "${aws_ecr_repository.repo["vllm"].repository_url}:${each.key}-sagemaker"
    model_data_source {
      s3_data_source {
        s3_uri           = "s3://${local.bucket}/models/${each.key}/"
        s3_data_type     = "S3Prefix"
        compression_type = "None"
      }
    }
  }
  depends_on = [aws_iam_role_policy.sagemaker, aws_cloudwatch_log_group.endpoint] # groups exist before any endpoint
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
  retention_in_days = var.retention_log_days
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
        { Effect = "Allow", Action = ["s3:ListBucket"], Resource = aws_s3_bucket.artifacts.arn,
        Condition = { StringLike = { "s3:prefix" = ["runs/*"] } } },
        { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], Resource = "${aws_s3_bucket.artifacts.arn}/runs/*" },
      ],
      length(local.endpoint_arns) > 0 ? [{ Effect = "Allow", Action = ["sagemaker:InvokeEndpoint", "sagemaker:InvokeEndpointWithResponseStream", "sagemaker:DescribeEndpoint"],
      Resource = local.endpoint_arns }] : [],
      length(var.kms_sign_key_arns) > 0 ? [{ Effect = "Allow", Action = ["kms:Sign"], Resource = var.kms_sign_key_arns }] : [],
      length(var.bedrock_model_ids) > 0 ? [{
        # Converse is authorised by InvokeModel. A cross-region profile id ("eu.anthropic...") needs the
        # profile ARN plus the un-prefixed foundation model in every region it routes to; full ARNs pass through.
        Effect = "Allow", Action = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
        Resource = flatten([for m in var.bedrock_model_ids : startswith(m, "arn:") ? [m] : [
          "arn:aws:bedrock:*::foundation-model/${replace(m, "/^(us|us-gov|eu|apac|jp|au|ca|global)\\./", "")}",
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
    name      = "bench"
    image     = var.bench_image_ref != "" ? var.bench_image_ref : "${aws_ecr_repository.repo["bench"].repository_url}:${var.bench_image_tag}"
    essential = true
    # hard limit: the task stops itself even if nobody is watching it. SIGTERM first (the bench then syncs
    # its partial results to S3), SIGKILL after the grace period.
    entryPoint  = ["timeout", "-k", "${var.runner_stop_grace_seconds}s", "${var.runner_max_minutes}m", "python", "-m", "ocrbench.cli"]
    command     = ["--help"]
    stopTimeout = min(var.runner_stop_grace_seconds, 120) # ECS StopTask: Fargate allows at most 120 s
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

# ---------------------------------------------------------------------------
# Cost safety: EventBridge Scheduler deletes the GPU endpoint after its maximum lifetime
# (`ocrbench endpoint up` creates the one-off schedule; `endpoint down` removes it).
# ---------------------------------------------------------------------------
resource "aws_iam_role" "scheduler" {
  name_prefix          = "${substr(local.p, 0, 20)}-sched-"
  permissions_boundary = var.permissions_boundary_arn
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "scheduler.amazonaws.com" }
      Condition = { StringEquals = { "aws:SourceAccount" = var.account_id } }
    }]
  })
}

# Every endpoint of the stack, not just the configured ones: a schedule must still fire for an endpoint
# whose backend has since left the config.
resource "aws_iam_role_policy" "scheduler" {
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["sagemaker:DeleteEndpoint"]
      Resource = "arn:aws:sagemaker:${var.region}:${var.account_id}:endpoint/${local.p}-*"
    }]
  })
}

moved {
  from = aws_iam_role_policy.scheduler[0]
  to   = aws_iam_role_policy.scheduler
}
