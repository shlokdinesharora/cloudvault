#!/usr/bin/env bash
# Writes frontend/public/config.json from Terraform outputs (run after `terraform apply`).
set -euo pipefail
cd "$(dirname "$0")/../infra"
out=$(terraform output -json)
jq -n --argjson o "$out" '{region:$o.region.value, apiUrl:$o.api_url.value, clientId:$o.user_pool_client_id.value}' \
  > ../frontend/public/config.json
echo "wrote frontend/public/config.json"
