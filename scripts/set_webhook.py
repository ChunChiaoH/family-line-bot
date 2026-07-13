#!/usr/bin/env python
"""Point LINE's webhook at a Cloud Run URL and verify it.

Usage:
    python scripts/set_webhook.py <cloud-run-base-url>

Reads LINE_CHANNEL_ACCESS_TOKEN and WEBHOOK_PATH_TOKEN from .env. Computes the
full webhook URL, sets it via the LINE Messaging API, then runs LINE's
test-webhook call and prints a PASS/FAIL report. Secrets are never printed; the
path token is masked to its first 4 characters in output.
"""
import os
import sys

from dotenv import load_dotenv
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    SetWebhookEndpointRequest,
)
from linebot.v3.messaging.exceptions import ApiException


def mask(token: str) -> str:
    """Mask a token to its first 4 chars for safe display."""
    if not token:
        return ""
    return token[:4] + "***"


def build_webhook_url(base_url: str, path_token: str) -> str:
    base = base_url.rstrip("/")
    if path_token:
        return f"{base}/webhook/{path_token}"
    return f"{base}/webhook"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: python scripts/set_webhook.py <cloud-run-base-url>")
        return 1

    base_url = argv[1]
    load_dotenv()

    access_token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    path_token = os.environ.get("WEBHOOK_PATH_TOKEN", "")

    if not access_token:
        print("ERROR: LINE_CHANNEL_ACCESS_TOKEN missing from .env")
        return 1

    webhook_url = build_webhook_url(base_url, path_token)
    masked_url = build_webhook_url(base_url, mask(path_token))
    print(f"Target webhook URL: {masked_url}")

    config = Configuration(access_token=access_token)
    with ApiClient(config) as api_client:
        api = MessagingApi(api_client)

        # 1. Set the webhook endpoint.
        try:
            api.set_webhook_endpoint(
                SetWebhookEndpointRequest(endpoint=webhook_url)
            )
            print("Set webhook endpoint: OK")
        except ApiException as exc:
            print("Set webhook endpoint: ERROR")
            print(f"  status={exc.status} reason={exc.reason}")
            if exc.body:
                print(f"  detail={exc.body}")
            print("FAIL")
            return 1

        # 2. Test the webhook endpoint.
        try:
            result = api.test_webhook_endpoint()
        except ApiException as exc:
            print("Test webhook endpoint: ERROR")
            print(f"  status={exc.status} reason={exc.reason}")
            if exc.body:
                print(f"  detail={exc.body}")
            print("FAIL")
            return 1

    if result.success:
        print(f"PASS (LINE status_code={result.status_code})")
        return 0

    print("Test webhook endpoint reported failure:")
    print(f"  status_code={result.status_code}")
    print(f"  reason={result.reason}")
    print(f"  detail={result.detail}")
    print("FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
