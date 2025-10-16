#!/usr/bin/env python3
"""
Single-file decoder/patch runner consolidating:
- patches/monkey_patch_tgto123.py
- sitecustomize.py behavior
- run_pyc_patched.py main

Usage:
  python decode_pyc_single.py /path/to/module.pyc [args...]

This script installs the import hook, rewrites constants in the target .pyc
code object, executes it as __main__, and then performs best-effort global
patching and GC scanning to catch live functions.
"""

import sys
import os
import types
import functools
import gc
import importlib
import importlib.abc
import importlib.machinery
from importlib.machinery import SourcelessFileLoader
from typing import Callable, Any


# ===== Consolidated from patches/monkey_patch_tgto123.py =====
_REPLACEMENTS = (
    ("https://t.me/tgto123update/", "https://t.me/TG123Cloud/"),
    ("https://t.me/tgto123update", "https://t.me/TG123Cloud"),
    ("@tgto123update", "@TG123Cloud"),
    ("tgto123update", "TG123Cloud"),
)


def _transform_string(value: str) -> str:
    new_value = value
    for old, new in _REPLACEMENTS:
        if old in new_value:
            new_value = new_value.replace(old, new)
    return new_value


def _transform_const(value: Any) -> Any:
    if isinstance(value, str):
        return _transform_string(value)
    # Carefully rewrite the magic value 47 -> 8 to affect compiled constants
    if isinstance(value, int) and value == 47:
        return 8
    return value


def _rebuild_code_with_new_consts(code: types.CodeType, new_consts: tuple) -> types.CodeType:
    # Prefer the stable replace() API if available (Py 3.8+)
    if hasattr(code, "replace"):
        return code.replace(co_consts=new_consts)

    # Fallback: rebuild CodeType by Python version
    vi = sys.version_info
    if vi >= (3, 11):
        return types.CodeType(
            code.co_argcount,
            code.co_posonlyargcount,
            code.co_kwonlyargcount,
            code.co_nlocals,
            code.co_stacksize,
            code.co_flags,
            code.co_code,
            new_consts,
            code.co_names,
            code.co_varnames,
            code.co_filename,
            code.co_name,
            code.co_qualname,
            code.co_firstlineno,
            code.co_linetable,
            code.co_exceptiontable,
            code.co_freevars,
            code.co_cellvars,
        )
    elif vi >= (3, 8):  # 3.8 - 3.10
        return types.CodeType(
            code.co_argcount,
            code.co_posonlyargcount,
            code.co_kwonlyargcount,
            code.co_nlocals,
            code.co_stacksize,
            code.co_flags,
            code.co_code,
            new_consts,
            code.co_names,
            code.co_varnames,
            code.co_filename,
            code.co_name,
            code.co_firstlineno,
            code.co_lnotab,
            code.co_freevars,
            code.co_cellvars,
        )
    else:  # 3.7 and below
        return types.CodeType(
            code.co_argcount,
            code.co_kwonlyargcount,
            code.co_nlocals,
            code.co_stacksize,
            code.co_flags,
            code.co_code,
            new_consts,
            code.co_names,
            code.co_varnames,
            code.co_filename,
            code.co_name,
            code.co_firstlineno,
            code.co_lnotab,
            code.co_freevars,
            code.co_cellvars,
        )


def _rewrite_code_object_consts(code: types.CodeType, transform: Callable[[Any], Any]) -> types.CodeType:
    changed = False
    new_consts_list = []

    for const in code.co_consts:
        if isinstance(const, (str, int)):
            new_const = transform(const)
            if new_const != const:
                changed = True
            new_consts_list.append(new_const)
        elif isinstance(const, types.CodeType):
            nested = _rewrite_code_object_consts(const, transform)
            if nested is not const:
                changed = True
            new_consts_list.append(nested)
        else:
            new_consts_list.append(const)

    if not changed:
        return code

    return _rebuild_code_with_new_consts(code, tuple(new_consts_list))


def _patch_function(fn: types.FunctionType) -> bool:
    try:
        new_code = _rewrite_code_object_consts(fn.__code__, _transform_const)
        if new_code is not fn.__code__:
            fn.__code__ = new_code
            changed = True
        else:
            changed = False

        # Patch defaults and kwdefaults that may carry strings/ids
        if fn.__defaults__:
            new_defaults = tuple(_transform_const(v) for v in fn.__defaults__)
            if new_defaults != fn.__defaults__:
                fn.__defaults__ = new_defaults
                changed = True
        if fn.__kwdefaults__:
            new_kw = {k: _transform_const(v) for k, v in fn.__kwdefaults__.items()}
            if new_kw != fn.__kwdefaults__:
                fn.__kwdefaults__ = new_kw
                changed = True

        return changed
    except Exception:
        return False


def _patch_property(prop: property) -> bool:
    changed = False
    if prop.fget and isinstance(prop.fget, types.FunctionType):
        changed |= _patch_function(prop.fget)
    if prop.fset and isinstance(prop.fset, types.FunctionType):
        changed |= _patch_function(prop.fset)
    if prop.fdel and isinstance(prop.fdel, types.FunctionType):
        changed |= _patch_function(prop.fdel)
    return changed


def _patch_class(cls: type) -> int:
    changed_count = 0
    for name, attr in vars(cls).items():
        if isinstance(attr, (staticmethod, classmethod)):
            func = attr.__func__
            if isinstance(func, types.FunctionType) and _patch_function(func):
                changed_count += 1
        elif isinstance(attr, types.FunctionType):
            if _patch_function(attr):
                changed_count += 1
        elif isinstance(attr, property):
            if _patch_property(attr):
                changed_count += 1
    return changed_count


essential_attrs = (types.FunctionType, type)


def apply_patch(module_or_name: Any) -> Any:
    """
    Apply the monkey patch to a module object or importable module name.

    - Sets module.newest_id = 8 if present (creates if absent)
    - Rewrites string constants in all functions/classes:
        '@tgto123update'  -> '@TG123Cloud'
        'tgto123update'   -> 'TG123Cloud'
    Returns the module for convenience.
    """
    module = importlib.import_module(module_or_name) if isinstance(module_or_name, str) else module_or_name

    # 1) Force newest_id = 8 if attribute exists or looks relevant
    try:
        if hasattr(module, "newest_id"):
            setattr(module, "newest_id", 8)
    except Exception:
        pass

    # 2) Patch module-level string attributes directly (best-effort)
    for key, value in list(vars(module).items()):
        if isinstance(value, str):
            new_value = _transform_string(value)
            if new_value != value:
                try:
                    setattr(module, key, new_value)
                except Exception:
                    pass

    # 3) Patch functions and classes
    seen_objs = set()

    def _safe_id(obj: Any) -> int:
        try:
            return id(obj)
        except Exception:
            return -1

    changed_total = 0
    for name, obj in list(vars(module).items()):
        oid = _safe_id(obj)
        if oid in seen_objs:
            continue
        seen_objs.add(oid)

        if isinstance(obj, types.FunctionType):
            if _patch_function(obj):
                changed_total += 1
        elif isinstance(obj, type):
            changed_total += _patch_class(obj)

    # 4) Wrap module-level send_message to sanitize outbound text
    def _wrap_callable_string_transform(func: Any) -> Any:
        if not callable(func):
            return func
        if getattr(func, "__patched_tgto123__", False):
            return func

        def _transform_arg(v: Any) -> Any:
            if isinstance(v, str):
                return _transform_string(v)
            return v

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            new_args = tuple(_transform_arg(a) for a in args)
            new_kwargs = {k: _transform_arg(v) for k, v in kwargs.items()}
            return func(*new_args, **new_kwargs)

        try:
            wrapper.__name__ = getattr(func, "__name__", "send_message")
        except Exception:
            pass
        setattr(wrapper, "__patched_tgto123__", True)
        return wrapper

    try:
        if hasattr(module, "send_message"):
            setattr(module, "send_message", _wrap_callable_string_transform(getattr(module, "send_message")))
    except Exception:
        pass

    # 5) Wrap bot.send_message if present
    try:
        bot = getattr(module, "bot", None)
        if bot is not None and hasattr(bot, "send_message"):
            orig = getattr(bot, "send_message")
            wrapped = _wrap_callable_string_transform(orig)
            try:
                setattr(bot, "send_message", wrapped)
            except Exception:
                pass
    except Exception:
        pass

    # 6) Special-case: patch telebot library methods when this is the telebot module
    try:
        if getattr(module, "__name__", "") == "telebot":
            _patch_telebot_module(module)
    except Exception:
        pass

    return module


def apply_patch_by_name(module_name: str) -> Any:
    return apply_patch(module_name)


def apply_patch_globally() -> int:
    """
    Patch all currently loaded modules in sys.modules.
    - Rewrites function/class code-string constants process-wide
    - Sets newest_id=8 on any module that defines it
    Returns the number of modules processed (best-effort).
    """
    processed = 0
    for module in list(sys.modules.values()):
        if module is None:
            continue
        # Avoid patching our own patch definitions repeatedly
        if getattr(module, "__name__", "").startswith(__name__):
            continue
        try:
            apply_patch(module)
            processed += 1
        except Exception:
            # best-effort; skip modules that cannot be inspected
            continue

    # Also try patching telebot if available now
    try:
        tb = sys.modules.get("telebot")
        if tb is not None:
            _patch_telebot_module(tb)
    except Exception:
        pass
    return processed


class _LoaderWrapper(importlib.abc.Loader):
    def __init__(self, wrapped_loader: importlib.abc.Loader):
        self._wrapped_loader = wrapped_loader

    def create_module(self, spec):  # optional
        if hasattr(self._wrapped_loader, "create_module"):
            return self._wrapped_loader.create_module(spec)
        return None

    def exec_module(self, module):
        fullname = getattr(module, "__name__", None)
        # If loader can provide code, fetch and exec our transformed code BEFORE execution
        try:
            get_code = getattr(self._wrapped_loader, "get_code", None)
            if callable(get_code) and fullname:
                code_obj = get_code(fullname)
                if isinstance(code_obj, types.CodeType):
                    transformed = _rewrite_code_object_consts(code_obj, _transform_const)
                    exec(transformed, module.__dict__)
                    # best-effort post-fix for anything created at runtime
                    try:
                        apply_patch(module)
                    except Exception:
                        pass
                    return
        except Exception:
            # If anything fails, fall back to normal execution then patch
            pass

        # Fallback path: run original exec then patch
        self._wrapped_loader.exec_module(module)
        try:
            apply_patch(module)
            # If telebot just loaded, patch its classes
            if getattr(module, "__name__", "") == "telebot":
                _patch_telebot_module(module)
        except Exception:
            pass


class _MetaPathPatcher(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        # Skip stdlib site hooks
        if fullname == "site" or fullname == "sitecustomize":
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec and spec.loader and not isinstance(spec.loader, _LoaderWrapper):
            spec.loader = _LoaderWrapper(spec.loader)
            return spec
        return None


def install_import_hook(prepend: bool = True) -> None:
    """Install a meta_path finder to patch modules right after import."""
    for finder in sys.meta_path:
        if isinstance(finder, _MetaPathPatcher):
            return
    if prepend:
        sys.meta_path.insert(0, _MetaPathPatcher())
    else:
        sys.meta_path.append(_MetaPathPatcher())


def apply_patch_via_gc() -> int:
    """Best-effort, walk all live function objects and patch their code/defaults."""
    count = 0
    try:
        for obj in gc.get_objects():
            try:
                if isinstance(obj, types.FunctionType):
                    if _patch_function(obj):
                        count += 1
            except Exception:
                continue
    except Exception:
        pass
    return count


def _patch_telebot_module(telebot_module: Any) -> bool:
    """Patch TeleBot methods to sanitize outbound/inbound text & chat ids."""
    try:
        cls = getattr(telebot_module, "TeleBot", None)
        if cls is None:
            return False

        def _wrap_method(name: str) -> bool:
            meth = getattr(cls, name, None)
            if not callable(meth) or getattr(meth, "__patched_tgto123__", False):
                return False

            @functools.wraps(meth)
            def wrapper(self, *args, **kwargs):
                new_args = tuple(_transform_const(a) if isinstance(a, str) else a for a in args)
                new_kwargs = {k: (_transform_const(v) if isinstance(v, str) else v) for k, v in kwargs.items()}
                return meth(self, *new_args, **new_kwargs)

            setattr(wrapper, "__patched_tgto123__", True)
            try:
                setattr(cls, name, wrapper)
                return True
            except Exception:
                return False

        changed = False
        changed |= _wrap_method("send_message")
        changed |= _wrap_method("get_chat")
        return changed
    except Exception:
        return False


# ===== Consolidated sitecustomize.py behavior =====
# We'll install the import hook and attempt a pass of patching for already-loaded modules
# before executing the provided .pyc. We'll also GC-scan afterwards.


# ===== Main runner (from run_pyc_patched.py) =====

def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python decode_pyc_single.py /path/to/module.pyc [args...]", file=sys.stderr)
        return 2

    pyc_path = sys.argv[1]
    argv_rest = sys.argv[2:]

    # Prepare runtime args as if executing script
    sys.argv = [pyc_path] + argv_rest

    # 1) Install import hook BEFORE executing target code so future imports are transformed
    try:
        install_import_hook()
    except Exception:
        pass

    # 2) Best-effort patch of already-loaded modules (like sitecustomize)
    try:
        apply_patch_globally()
    except Exception:
        pass

    # 3) Load compiled code object
    loader = SourcelessFileLoader("__main__", pyc_path)
    code_obj = loader.get_code("__main__")
    if not isinstance(code_obj, types.CodeType):
        print("Failed to load code object from .pyc", file=sys.stderr)
        return 1

    # 4) Transform constants (47->8, strings replacements) before exec
    transformed = _rewrite_code_object_consts(code_obj, _transform_const)

    # 5) Create __main__ module namespace and execute
    module = types.ModuleType("__main__")
    module.__file__ = pyc_path
    module.__package__ = None
    module.__builtins__ = __builtins__
    sys.modules["__main__"] = module

    exec(transformed, module.__dict__)

    # 6) Best-effort global patching and GC scan after execution too
    try:
        apply_patch_globally()
        apply_patch_via_gc()
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
