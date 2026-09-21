"""PESU Agent Security Package."""

from pesu_agent.security.credential_store import (
    CREDENTIAL_STORAGE_SCOPE,
    OPENROUTER_KEY_STORED_PLAINTEXT,
    CREDENTIAL_PRECEDENCE,
    CredentialStore,
    default_credential_store,
    get_openrouter_api_key,
    save_openrouter_api_key,
    remove_openrouter_api_key,
    get_openrouter_status,
)

__all__ = [
    "CREDENTIAL_STORAGE_SCOPE",
    "OPENROUTER_KEY_STORED_PLAINTEXT",
    "CREDENTIAL_PRECEDENCE",
    "CredentialStore",
    "default_credential_store",
    "get_openrouter_api_key",
    "save_openrouter_api_key",
    "remove_openrouter_api_key",
    "get_openrouter_status",
]
