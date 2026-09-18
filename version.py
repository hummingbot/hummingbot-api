"""The API server's own version number.

It lives in its own module, with no imports of its own, because main.py imports every
router: a router that needs the version -- /system/info reports it -- cannot import it
back from main.py without a circular import. main.py re-exports it, and the Docker
build workflow reads the assignment below with grep, so keep it a plain literal.
"""

VERSION = "1.0.1"
