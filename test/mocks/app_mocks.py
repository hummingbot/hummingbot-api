"""Apply shared mocks for tests that import this module.

Importing this module will call `setup_common_mocks()` which ensures
lightweight mock modules exist in `sys.modules` before application code
imports run. This mirrors the JS pattern where tests import mocks at the
top of the test file so module mocking happens before app code is loaded.
"""
from .shared_mocks import setup_common_mocks


# Apply the mocks immediately on import
setup_common_mocks()

# Export nothing; presence of this module in test imports is the side effect
__all__ = []
