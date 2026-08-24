#!/bin/bash

# Configuration
API_URL="http://localhost:8092/jobs"
PAYLOAD='{
  "stage": "arc",
  "pack": "ashiorid",
  "profile": "heavy",
  "dry_run": true
}'

echo "-------------------------------------------------------"
echo "🚀 Sending Test Job to Generator: [Stage: ALL]"
echo "Target URL: $API_URL"
echo "Payload: $PAYLOAD"
echo "-------------------------------------------------------"

# Execute the request
# We use -s for silent and -S to show errors
RESPONSE=$(curl -s -S -X POST "$API_URL" \
     -H "Content-Type: application/json" \
     -d "$PAYLOAD")

if [ $? -eq 0 ]; then
    echo "✅ Request Successful!"
    echo "Response from API:"
    echo "$RESPONSE" | jq . 2>/dev/null || echo "$RESPONSE"
else
    echo "❌ Request Failed!"
    exit 1
fi

echo "-------------------------------------------------------"
