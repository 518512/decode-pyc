#!/usr/bin/env python3
import sys
import os
import types
from importlib.machinery import SourcelessFileLoader

# Ensure local workspace (containing patches/) is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from patches.monkey_patch_tgto123 import (
    install_import_hook,
    apply_patch_globally,
    apply_patch_via_gc,
    _rewrite_code_object_consts,
    _transform_const,
)


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python run_pyc_patched.py /path/to/module.pyc [args...]", file=sys.stderr)
        return 2

    pyc_path = sys.argv[1]
    argv_rest = sys.argv[2:]

    # Prepare runtime args as if executing script
    sys.argv = [pyc_path] + argv_rest

    # Install import hook BEFORE executing target code so future imports are transformed
    try:
        install_import_hook()
    except Exception:
        pass

    # Load compiled code object
    loader = SourcelessFileLoader("__main__", pyc_path)
    code_obj = loader.get_code("__main__")
    if not isinstance(code_obj, types.CodeType):
        print("Failed to load code object from .pyc", file=sys.stderr)
        return 1

    # Transform constants (47->8, strings replacements) before exec
    transformed = _rewrite_code_object_consts(code_obj, _transform_const)

    # Create __main__ module namespace and execute
    module = types.ModuleType("__main__")
    module.__file__ = pyc_path
    module.__package__ = None
    module.__builtins__ = __builtins__
    sys.modules["__main__"] = module

    exec(transformed, module.__dict__)

    # Best-effort global patching after execution too
    try:
        apply_patch_globally()
        apply_patch_via_gc()
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
