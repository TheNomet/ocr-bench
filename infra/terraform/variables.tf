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
variable "runner_max_minutes" {
  description = "Hard limit for one bench task"
  type        = number
  default     = 240
}
variable "bench_image_tag" {
  type    = string
  default = "latest"
}

# ---- self-hosted models ----
variable "selfhosted" {
  description = "Self-hosted backends (config kind: selfhosted): name -> pinned image (repo@sha256:..., empty = <name>-sagemaker tag) and network isolation. Endpoint permissions cover every name here."
  type = map(object({
    image_ref         = string
    network_isolation = bool
  }))
  default = {}
}
variable "create_selfhosted_models" {
  description = "Create the SageMaker models (needs the images in ECR and weights in S3 first)"
  type        = bool
  default     = false
}
variable "bench_image_ref" {
  description = "Pinned bench image (repo@sha256:...); empty = use bench_image_tag"
  type        = string
  default     = ""
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
