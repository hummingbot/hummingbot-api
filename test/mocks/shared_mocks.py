"""Shared mocks used by tests.

This module provides simple mock objects that can be inserted into
`sys.modules` at import time so application imports resolve to test doubles
during unit tests. Tests should import `test.mocks.app_mocks` which calls
`setup_common_mocks()` to apply these replacements before app modules
are imported.
"""
from types import SimpleNamespace
import sys


# Minimal logger mock
mock_logger = SimpleNamespace(
    info=lambda *a, **k: None,
    error=lambda *a, **k: None,
    warn=lambda *a, **k: None,
    debug=lambda *a, **k: None,
)


# Minimal Config Manager mock
class _ConfigManagerMock:
    def __init__(self):
        self._store = {
            'server.port': 15888,
            'server.docsPort': 19999,
            'server.fastifyLogs': False,
        }

    def get(self, key, default=None):
        return self._store.get(key, default)

    def set(self, key, value):
        self._store[key] = value


mock_config_manager = SimpleNamespace(getInstance=lambda: SimpleNamespace(get=_ConfigManagerMock().get, set=_ConfigManagerMock().set))


# Chain config stubs
def get_solana_chain_config():
    return {
        'defaultNetwork': 'mainnet-beta',
        'defaultWallet': 'test-wallet',
        'rpcProvider': 'https://api.mainnet-beta.solana.com',
    }


def get_ethereum_chain_config():
    return {
        'defaultNetwork': 'mainnet',
        'defaultWallet': 'test-wallet',
        'rpcProvider': 'https://mainnet.infura.io/v3/test',
    }


def setup_common_mocks():
    """Insert lightweight mock modules into sys.modules so imports resolve.

    This is intentionally minimal — tests can do more thorough monkeypatching
    per-test when needed.
    """
    # services.logger -> module with `logger` attribute
    sys.modules.setdefault('services.logger', SimpleNamespace(logger=mock_logger, redact_url=lambda u: u))

    # services.config_manager_v2 -> ConfigManagerV2 shim
    sys.modules.setdefault('services.config_manager_v2', SimpleNamespace(ConfigManagerV2=mock_config_manager))

    # chains.*.config modules
    sys.modules.setdefault('chains.solana.solana.config', SimpleNamespace(getSolanaChainConfig=get_solana_chain_config))
    sys.modules.setdefault('chains.ethereum.ethereum.config', SimpleNamespace(getEthereumChainConfig=get_ethereum_chain_config))

    # filesystem token list reads are not patched here — tests may monkeypatch open() or functions that load files


__all__ = [
    'mock_logger',
    'mock_config_manager',
    'setup_common_mocks',
]
