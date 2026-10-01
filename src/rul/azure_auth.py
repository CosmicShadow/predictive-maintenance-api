"""One shared Azure credential.

DefaultAzureCredential tries, in order, environment variables, workload identity, managed
identity and then developer logins such as `az login`. In Azure Container Apps it picks up the
user-assigned managed identity named by AZURE_CLIENT_ID; on a laptop it uses your `az login`.
No keys or connection strings are involved either way.
"""

from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=1)
def get_credential():
    import logging

    from azure.identity import DefaultAzureCredential

    # The SDK logs every credential attempt and HTTP request at INFO; keep CLI output readable.
    logging.getLogger("azure").setLevel(logging.WARNING)
    return DefaultAzureCredential()
