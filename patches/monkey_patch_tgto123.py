import importlib
import sys
import types
from typing import Callable, Any


_REPLACEMENTS = (
    ("@tgto123update", "@TG123Cloud"),
    ("tgto123update", "TG123Cloud"),
)


def _transform_string(value: str) -> str:
    new_value = value
    for old, new in _REPLACEMENTS:
        if old in new_value:
            new_value = new_value.replace(old, new)
    return new_value


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


def _rewrite_code_object_strings(code: types.CodeType, transform: Callable[[str], str]) -> types.CodeType:
    changed = False
    new_consts_list = []

    for const in code.co_consts:
        if isinstance(const, str):
            new_const = transform(const)
            if new_const != const:
                changed = True
            new_consts_list.append(new_const)
        elif isinstance(const, types.CodeType):
            nested = _rewrite_code_object_strings(const, transform)
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
        new_code = _rewrite_code_object_strings(fn.__code__, _transform_string)
        if new_code is not fn.__code__:
            fn.__code__ = new_code
            return True
        return False
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

    # 1) Force newest_id = 8
    try:
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

    return module


def apply_patch_by_name(module_name: str) -> Any:
    return apply_patch(module_name)
