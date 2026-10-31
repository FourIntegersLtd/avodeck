#!/usr/bin/env bash
# Create the private S3 bucket for case files (ADR 0032).
# Run once per cell. Requires AWS CLI configured with the cell's IAM credentials.
#
# Usage:
#   ./infra/s3/create-bucket.sh                         # defaults: avodeck-uk-case-files in eu-west-2
#   ./infra/s3/create-bucket.sh my-bucket eu-west-2     # custom name and region
set -euo pipefail

BUCKET="${1:-avodeck-uk-case-files}"
REGION="${2:-eu-west-2}"

echo "Creating bucket ${BUCKET} in ${REGION}..."

# Create the bucket with a location constraint (us-east-1 does not accept one).
if [ "$REGION" = "us-east-1" ]; then
  aws s3api create-bucket --bucket "$BUCKET" --region "$REGION"
else
  aws s3api create-bucket \
    --bucket "$BUCKET" \
    --region "$REGION" \
    --create-bucket-configuration "LocationConstraint=${REGION}"
fi

# Block all public access.
aws s3api put-public-access-block \
  --bucket "$BUCKET" \
  --public-access-block-configuration \
    "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"

# Default server-side encryption (AES-256 / SSE-S3).
aws s3api put-bucket-encryption \
  --bucket "$BUCKET" \
  --server-side-encryption-configuration \
    '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"},"BucketKeyEnabled":true}]}'

# Deny any unencrypted transport.
aws s3api put-bucket-policy --bucket "$BUCKET" --policy "$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DenyInsecureTransport",
      "Effect": "Deny",
      "Principal": "*",
      "Action": "s3:*",
      "Resource": [
        "arn:aws:s3:::${BUCKET}",
        "arn:aws:s3:::${BUCKET}/*"
      ],
      "Condition": {
        "Bool": { "aws:SecureTransport": "false" }
      }
    }
  ]
}
EOF
)"

# Abort incomplete multipart uploads after 1 day.
aws s3api put-bucket-lifecycle-configuration \
  --bucket "$BUCKET" \
  --lifecycle-configuration \
    '{"Rules":[{"ID":"abort-incomplete-multipart","Status":"Enabled","Filter":{},"AbortIncompleteMultipartUpload":{"DaysAfterInitiation":1}}]}'

# No versioning (ADR 0032: immutability is in the database, not the bucket).

echo ""
echo "Done. Verify:"
echo "  aws s3api get-bucket-location --bucket ${BUCKET}"
echo "  aws s3api get-bucket-versioning --bucket ${BUCKET}   # should be empty"
echo "  aws s3api get-public-access-block --bucket ${BUCKET}"
echo ""
echo "Set in api/.env:"
echo "  STORAGE_BUCKET=${BUCKET}"
echo "  AWS_REGION=${REGION}"
echo "  AWS_ALLOWED_REGIONS=${REGION}"
