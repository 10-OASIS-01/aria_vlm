import httpx
import pytest

from aria_guidance.client import GuidanceClient


def client_with(handler, token="secret"):
    client = GuidanceClient("http://backend/aria", token=token)
    client.http = httpx.Client(
        base_url="http://backend/aria/",
        headers=client.http.headers,
        transport=httpx.MockTransport(handler),
    )
    return client


def test_plan_v1_sends_body_and_token():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        seen["url"] = str(request.url)
        seen["body"] = request.read()
        return httpx.Response(200, json={"status": "instruction"})

    client_with(handler).plan_v1("Clear the mat", [], [], live=False)
    assert seen["auth"] == "Bearer secret"
    assert seen["url"] == "http://backend/aria/v1/plan"
    assert b'"live":false' in seen["body"].replace(b" ", b"")


def test_error_keeps_server_detail():
    def handler(request):
        return httpx.Response(422, json={"detail": "Newest frame is 11.4 s old"})

    with pytest.raises(httpx.HTTPStatusError, match="422: Newest frame is 11.4 s old"):
        client_with(handler).plan_v1("Clear the mat", [], [])
