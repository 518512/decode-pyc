# Auto-loaded by Python on startup if on sys.path
from patches.monkey_patch_tgto123 import (
    install_import_hook,
    apply_patch_globally,
    apply_patch_via_gc,
)

# 1) Install import hook so future imports are transformed pre-exec
try:
    install_import_hook()
except Exception:
    pass

# 2) Patch already-loaded modules (best effort)
try:
    apply_patch_globally()
except Exception:
    pass

# 3) GC-scan to catch live functions created before hook
try:
    apply_patch_via_gc()
except Exception:
    pass
