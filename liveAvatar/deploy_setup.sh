#!/bin/bash
PROJECT_ID=$(gcloud config get-value project 2>/dev/null | grep -v 'WARNING' | grep -v 'Updates' | grep -v 'To take' | tail -n 1)
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format="value(projectNumber)")
API_KEY="${LIVEAVATAR_API_KEY:?Error: set LIVEAVATAR_API_KEY env var before running this script}"

# provision_secret NAME VALUE — create (or version) a Secret Manager secret
# and let the Cloud Run default service account read it.
provision_secret() {
    local name="$1" value="$2"
    if gcloud secrets describe "$name" >/dev/null 2>&1; then
        echo "Secret $name already exists. Adding new version..."
        echo -n "$value" | gcloud secrets versions add "$name" --data-file=-
    else
        echo "Creating secret $name..."
        echo -n "$value" | gcloud secrets create "$name" --data-file=-
    fi
    echo "Adding IAM policy binding for $name..."
    gcloud secrets add-iam-policy-binding "$name" \
        --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
        --role="roles/secretmanager.secretAccessor"
}

provision_secret LIVEAVATAR_API_KEY "$API_KEY"

# Tavily powers all Data Scout web search; without it the scout silently
# produces near-empty reports (search soft-fails to no results).
if [ -n "$TAVILY_API_KEY" ]; then
    provision_secret TAVILY_API_KEY "$TAVILY_API_KEY"
else
    echo "TAVILY_API_KEY not set — skipping (Data Scout web search will be disabled in prod unless provided some other way)."
fi
