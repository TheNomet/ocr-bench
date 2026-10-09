# All values come from `ocrbench tf-vars` (rendered from config/<name>.yaml) into
# .state/<name_prefix>/terraform.tfvars.json. See docs/configuration.md.

variable "name_prefix" { type = string }
variable "account_id" { type = string }
variable "region" { type = string }

variable "permissions_boundary_arn" {
  type    = string
  default = null
}

variable "tags" {
  type    = map(string)
  default = {}
}

# ---- network ----
variable "network_mode" {
  type = string
  validation {
    condition     = contains(["existing", "create"], var.network_mode)
    error_message = "network_mode must be existing or create"
  }
}
variable "existing_vpc_id" {
  type    = string
  default = null
}
variable "existing_vpc_name_tag" {
  type    = string
  default = null
}
variable "existing_subnet_ids" {
  type    = list(string)
  default = []
}
variable "existing_subnet_name_tag_glob" {
  type    = string
  default = null
}
variable "create_cidr" {
  type    = string
  default = "10.42.0.0/24"
}

# ---- runner ----
variable "ecs_cluster" {
  description = "\"create\" or the name of an existing ECS cluster"
  type        = string
  default     = "create"
}
variable "runner_cpu" {
  type    = number
  default = 2048
}
variable "runner_memory" {
  type    = number
  default = 4096
}
variable "bench_image_tag" {
  type    = string
  default = "latest"
}

# ---- OCR model ----
variable "create_ocr_model" {
  description = "Create the SageMaker model (needs the image in ECR and weights in S3 first)"
  type        = bool
  default     = false
}
variable "ocr_image_tag" {
  type    = string
  default = "sagemaker"
}
variable "model_s3_prefix" {
  type    = string
  default = "models/ocr"
}
variable "sagemaker_network_isolation" {
  type    = bool
  default = true
}
variable "max_num_seqs" {
  type    = string
  default = "64"
}

# ---- permissions for LLM backends ----
variable "kms_sign_key_arns" {
  description = "KMS keys the runner may kms:Sign with (oauth2_kms_jwt gateway auth)"
  type        = list(string)
  default     = []
}
variable "bedrock_model_ids" {
  description = "Bedrock model/inference-profile ids the runner may invoke"
  type        = list(string)
  default     = []
}
