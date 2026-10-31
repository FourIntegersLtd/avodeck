# S3 bucket for case files (ADR 0032)

One private bucket per cell holds every case file (upload chunks, page images, PDFs,
reports, invoices, transcripts, exports, trial packs, templates and signatures).
Files are encrypted per-case by the app before upload; SSE-S3 is a second layer.

## Create the bucket

The script needs admin-level S3 permissions (create, put-policy, put-lifecycle).
Run it with an admin account or create the bucket in the AWS console and apply
the policy and lifecycle rule manually (see the script for the values).

```bash
# Requires AWS CLI configured with admin credentials (not the app's scoped user).
./infra/s3/create-bucket.sh                         # avodeck-uk-case-files in eu-west-2
./infra/s3/create-bucket.sh my-bucket eu-west-2     # custom name
```

The script sets: Block Public Access (all four), SSE-S3 default encryption,
a bucket policy denying unencrypted transport, and a lifecycle rule aborting
incomplete multipart uploads after 1 day. No versioning (ADR 0032).

## IAM policy

`iam-policy.json` grants the minimum permissions the app needs. Attach it to
the cell's scoped IAM user (the same one Bedrock uses). Replace the bucket
name if you used a custom one.

## Deploy env

Set in `api/.env` (or the deploy platform's env vars):

```
STORAGE_BUCKET=avodeck-uk-case-files
AWS_REGION=eu-west-2
AWS_ALLOWED_REGIONS=eu-west-2
AWS_ACCESS_KEY_ID=<cell IAM user>
AWS_SECRET_ACCESS_KEY=<cell IAM user>
```

`S3_ENDPOINT_URL` is local-only (moto/localstack) and refused in production.
