import asyncio
from types import SimpleNamespace

import httpx
import pytest
from prime_sandboxes.core import APIError

from scripts.verify_multisandbox_run import missing_or_terminated


@pytest.mark.parametrize("status", [404, 401])
def test_provider_deletion_requires_real_not_found(status):
    class Client:
        async def get_lan_network(self, identity):
            response = httpx.Response(status, request=httpx.Request("GET", "https://example.test/network"))
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError:
                raise APIError("Network request failed")
    if status == 404:
        assert asyncio.run(missing_or_terminated(Client(), "owned-lan", network=True)) == "deleted"
    else:
        with pytest.raises(APIError):
            asyncio.run(missing_or_terminated(Client(), "owned-lan", network=True))


def test_running_vm_is_not_treated_as_released():
    class Client:
        async def get(self, identity):
            return SimpleNamespace(status="RUNNING")
    with pytest.raises(AssertionError, match="not released"):
        asyncio.run(missing_or_terminated(Client(), "owned-vm"))
