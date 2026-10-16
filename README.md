# CloudVault: distributed file processing and sharing platform

Serverless AWS platform: upload files, process them in parallel (scan, checksum, compress, index), share them via expiring password-protected links. Everything is Infrastructure as Code and deployed by CI/CD.

```
cloudvault/
├── infra/            Terraform: S3, SQS(+DLQ), Lambda x2, API Gateway, Cognito, DynamoDB, SNS, CloudFront, CloudWatch, Budgets
├── bootstrap/        one-time: Terraform state bucket + GitHub OIDC deploy role
├── backend/api/      API Lambda (routes, sharing, presigned URLs)
├── backend/processor/  SQS-driven processing worker
├── backend/tests/    pytest unit tests
├── frontend/         React (Vite) single-page app
├── .github/workflows CI (tests, tf validate, build) + Deploy
└── docs/ARCHITECTURE.md  diagram and design decisions
```

## Course checklist
| Requirement | Where |
|---|---|
| >= 4 managed services | S3, Lambda, SQS, SNS, API Gateway, Cognito, DynamoDB, CloudFront, CloudWatch, Budgets |
| IaC | `infra/` (Terraform) |
| CI/CD | `.github/workflows/` (GitHub Actions) |
| Budget alerts | `aws_budgets_budget` in `infra/main.tf` ($5 default) |
| Repo link for the design doc | push this folder to GitHub |

## Deploy (first time, ~15 min)
Prereqs: AWS account, AWS CLI configured, Terraform >= 1.10, Node 20, jq, a GitHub repo.

1. **Bootstrap** (once, local credentials):
   ```bash
   cd bootstrap && terraform init && terraform apply -var github_repo=<user>/<repo>
   ```
   Note the `state_bucket` and `deploy_role_arn` outputs.
2. **GitHub secrets** (repo -> Settings -> Secrets -> Actions): `AWS_ROLE_ARN`, `TF_STATE_BUCKET`, `ALERT_EMAIL`.
3. **Push to `main`**. The Deploy workflow runs tests, `terraform apply`, builds the SPA and publishes it. The site URL appears in the run summary.
4. **Confirm the SNS email** AWS sends to `ALERT_EMAIL` (otherwise no alerts).

### Manual deploy (no CI)
```bash
cd infra && terraform init && terraform apply -var alert_email=you@example.com
cd .. && ./scripts/make-config.sh && cd frontend && npm ci && npm run build
aws s3 sync dist s3://$(terraform -chdir=../infra output -raw frontend_bucket) --delete
```

### Local frontend dev
`./scripts/make-config.sh && cd frontend && npm install && npm run dev` (talks to the deployed API).

### Tests
`pip install -r backend/requirements-dev.txt && python -m pytest backend/tests`

### Tear down (stops all cost)
From GitHub: Actions -> **Destroy** -> Run workflow -> type `destroy`. Or locally:
```bash
cd infra && terraform destroy -var alert_email=you@example.com
```

## API
| Route | Auth | Purpose |
|---|---|---|
| `POST /files/upload-url` | JWT | Register file, get presigned POST |
| `GET /files?q=` | JWT | List/search own files |
| `GET /files/{id}` | JWT | File + active share links |
| `GET /files/{id}/download?variant=original\|compressed\|thumbnail` | JWT | 60 s download URL |
| `DELETE /files/{id}` | JWT | Delete file, variants and links |
| `POST /files/{id}/share` | JWT | `{expires_in_hours, max_downloads?, password?}` |
| `DELETE /shares/{token}` | JWT | Revoke link |
| `GET /s/{token}` | public | Link metadata |
| `POST /s/{token}/download` | public | `{password?}` -> download URL |

## Demo script (Lab 12)
1. Show the GitHub Actions run and the Terraform-created resources in the console.
2. Sign up, verify the emailed code, sign in.
3. Upload several files at once; watch `PENDING -> PROCESSING -> DONE`, then the CloudWatch dashboard (queue depth, invocations).
4. Upload a text file with the EICAR test string: it ends `QUARANTINED`, SNS emails you. Upload a renamed `.exe`: `BLOCKED`.
5. Search by file name and by text content.
6. Share with 1 download and a password; open the link in a private window; second download is refused. Revoke another link.
7. Failure demo: make the processor fail (e.g. temporarily set the Lambda env `BUCKET` to a wrong name), upload, show retries -> DLQ -> alarm email -> `FAILED`.

## Known limits and next steps
- Malware scanning is signature-based (EICAR + executable blocking). A real deployment would run ClamAV in a container-image Lambda or ECS task behind the same queue.
- Notifications go to the SNS topic subscribers (the team email). Per-user email would need SES with a verified domain.
- Thumbnails need a Pillow Lambda layer: build one for Python 3.12 / x86_64 and pass `-var pillow_layer_arn=...`.
- Files stuck in `PENDING_UPLOAD` (never uploaded) are not auto-expired yet.
- Deployed to AWS (ap-south-1) through the GitHub Actions pipeline and tested on the live environment; the Destroy workflow tears everything down.
