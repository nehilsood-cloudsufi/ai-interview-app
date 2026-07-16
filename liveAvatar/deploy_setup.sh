#!/bin/bash
PROJECT_ID=$(gcloud config get-value project 2>/dev/null | grep -v 'WARNING' | grep -v 'Updates' | grep -v 'To take' | tail -n 1)
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format="value(projectNumber)")
API_KEY="${LIVEAVATAR_API_KEY:?Error: set LIVEAVATAR_API_KEY env var before running this script}"

if gcloud secrets describe LIVEAVATAR_API_KEY >/dev/null 2>&1; then
    echo "Secret already exists. Adding new version..."
    echo -n "$API_KEY" | gcloud secrets versions add LIVEAVATAR_API_KEY --data-file=-
else
    echo "Creating new secret..."
    echo -n "$API_KEY" | gcloud secrets create LIVEAVATAR_API_KEY --data-file=-
fi

echo "Adding IAM policy binding..."
gcloud secrets add-iam-policy-binding LIVEAVATAR_API_KEY \
    --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
    --role="roles/secretmanager.secretAccessor"
