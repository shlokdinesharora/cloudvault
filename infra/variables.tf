variable "region" {
  type    = string
  default = "ap-south-1"
}
variable "env" {
  type    = string
  default = "dev"
}
variable "alert_email" {
  type        = string
  description = "Receives budget alerts, alarms and processing notifications (SNS needs a one-time confirmation click)."
}
variable "budget_limit_usd" {
  type    = number
  default = 5
}
variable "max_upload_mb" {
  type    = number
  default = 100
}
variable "pillow_layer_arn" {
  type        = string
  default     = ""
  description = "Optional Lambda layer containing Pillow; enables image thumbnails."
}
