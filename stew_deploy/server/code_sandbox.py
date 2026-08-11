"""
S.T.E.W Code Execution Sandbox — Safe Python execution for agentic tasks.

Inspired by Kimi's code execution: the LLM writes Python code, we run it
in a restricted sandbox with no network, no file system access, limited
built-ins, and a strict timeout. Output (text, data, charts) is returned
to the LLM for the next reasoning step.

Safety:
  - No imports of os, sys, subprocess, socket, shutil, pathlib, etc.
  - Only whitelisted modules: math, json, re, datetime, statistics, collections,
    itertools, random, string, textwrap, decimal, fractions, hashlib (non-crypto)
  - No __builtins__ access to open, exec, eval, compile, __import__
  - 10-second CPU timeout
  - 50KB max output
  - Captures stdout + the value of the last expression
  - matplotlib for charts (saved as base64 PNG)
"""
import io
import sys
import ast
import base64
import logging
import signal
import traceback
from contextlib import redirect_stdout, redirect_stderr
from typing import Any

logger = logging.getLogger(__name__)

# Modules the sandbox is allowed to import
ALLOWED_MODULES = {
    "math", "json", "re", "datetime", "statistics", "collections",
    "itertools", "random", "string", "textwrap", "decimal", "fractions",
    "hashlib", "unicodedata", "operator", "functools", "bisect",
    "copy", "pprint", "csv", "io",
}

# Modules that need special setup
OPTIONAL_MODULES = {}
try:
    import matplotlib
    matplotlib.use("Agg")
    OPTIONAL_MODULES["matplotlib"] = True
    OPTIONAL_MODULES["matplotlib.pyplot"] = True
    OPTIONAL_MODULES["numpy"] = True
except ImportError:
    pass

try:
    import pandas as pd
    OPTIONAL_MODULES["pandas"] = True
except ImportError:
    pass

# Built-ins available in the sandbox (name -> actual builtin function)
import builtins as _builtins

_BUILTIN_NAMES = [
    "abs", "all", "any", "ascii", "bin", "bool", "bytearray", "bytes",
    "callable", "chr", "complex", "dict", "divmod", "enumerate", "filter",
    "float", "format", "frozenset", "getattr", "hasattr", "hash", "hex",
    "id", "int", "isinstance", "issubclass", "iter", "len", "list", "map",
    "max", "min", "next", "object", "oct", "ord", "pow", "print", "property",
    "range", "repr", "reversed", "round", "set", "setattr", "slice",
    "sorted", "str", "sum", "tuple", "type", "vars", "zip",
    "True", "False", "None",
    "Exception", "ValueError", "TypeError", "KeyError", "IndexError",
    "AttributeError", "RuntimeError", "StopIteration", "ZeroDivisionError",
    "OverflowError", "NameError", "ImportError", "ModuleNotFoundError",
    "NotImplementedError", "ArithmeticError", "LookupError", "AssertionError",
    "Warning", "classmethod", "staticmethod", "delattr", "dir", "super",
]

SAFE_BUILTINS = {}
for name in _BUILTIN_NAMES:
    if hasattr(_builtins, name):
        SAFE_BUILTINS[name] = getattr(_builtins, name)


class _SafeImporter:
    """Restricts imports to whitelisted modules only."""
    def __init__(self, allowed: set, optional: dict):
        self.allowed = allowed
        self.optional = optional

    def __call__(self, name, *args, **kwargs):
        root = name.split(".")[0]
        if name in self.allowed or root in self.allowed:
            return __import__(name, *args, **kwargs)
        if name in self.optional or root in self.optional:
            try:
                return __import__(name, *args, **kwargs)
            except ImportError:
                raise ImportError(f"Module '{name}' is not available in the sandbox")
        raise ImportError(
            f"Module '{name}' is not allowed in the S.T.E.W sandbox. "
            f"Allowed: {', '.join(sorted(self.allowed))}"
        )


def _validate_code(code: str) -> tuple:
    """Check that code doesn't contain forbidden patterns."""
    forbidden = [
        ("__import__", "Use of __import__ is not allowed"),
        ("__builtins__", "Access to __builtins__ is not allowed"),
        ("__subclasses__", "Access to __subclasses__ is not allowed"),
        ("__bases__", "Access to __bases__ is not allowed"),
        ("__mro__", "Access to __mro__ is not allowed"),
        ("compile(", "Use of compile() is not allowed"),
        ("eval(", "Use of eval() is not allowed"),
        ("exec(", "Use of exec() is not allowed"),
        ("open(", "Use of open() is not allowed"),
        ("input(", "Use of input() is not allowed"),
        ("breakpoint(", "Use of breakpoint() is not allowed"),
        ("exit(", "Use of exit() is not allowed"),
        ("quit(", "Use of quit() is not allowed"),
    ]
    for pattern, msg in forbidden:
        if pattern in code:
            return False, msg
    return True, ""


class _TimeoutError(Exception):
    pass


def _timeout_handler(signum, frame):
    raise _TimeoutError("Code execution timed out (10 second limit)")


def execute_code(code: str, timeout: int = 10) -> dict:
    """
    Execute Python code in a restricted sandbox.
    Returns dict with success, stdout, result, error, figures, execution_time.
    """
    import time as _time
    start = _time.time()

    # Validate code
    ok, msg = _validate_code(code)
    if not ok:
        return {
            "success": False, "stdout": "", "result": None,
            "error": msg, "traceback": None, "figures": [],
            "execution_time": round(_time.time() - start, 3),
        }

    # Parse the code to find the last expression statement
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return {
            "success": False, "stdout": "", "result": None,
            "error": f"Syntax error: {e}", "traceback": None,
            "figures": [], "execution_time": round(_time.time() - start, 3),
        }

    # Split into body + last expression
    last_expr = None
    body = list(tree.body)
    if body and isinstance(body[-1], ast.Expr):
        last_expr = ast.Expression(body=body[-1].value)
        body = body[:-1]

    exec_code = ast.Module(body=body, type_ignores=[])

    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    result_val = None
    figures = []

    # Build the sandbox globals
    safe_globals = {
        "__builtins__": {
            **SAFE_BUILTINS,
            "__import__": _SafeImporter(ALLOWED_MODULES, OPTIONAL_MODULES),
        },
        "__name__": "__sandbox__",
    }

    # Add matplotlib/numpy/pandas
    if OPTIONAL_MODULES.get("matplotlib"):
        try:
            import matplotlib.pyplot as plt
            safe_globals["plt"] = plt
        except:
            pass
    if OPTIONAL_MODULES.get("numpy"):
        try:
            import numpy as np
            safe_globals["np"] = np
        except:
            pass
    if OPTIONAL_MODULES.get("pandas"):
        safe_globals["pd"] = pd

    try:
        old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(timeout)

        with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
            if body:
                compiled_body = compile(exec_code, "<sandbox>", "exec")
                exec(compiled_body, safe_globals)

            if last_expr:
                compiled_expr = compile(last_expr, "<sandbox>", "eval")
                result_val = eval(compiled_expr, safe_globals)

        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)

        # Capture matplotlib figures
        if OPTIONAL_MODULES.get("matplotlib"):
            try:
                import matplotlib.pyplot as plt
                fig_nums = plt.get_fignums()
                for fnum in fig_nums:
                    fig = plt.figure(fnum)
                    buf = io.BytesIO()
                    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
                    buf.seek(0)
                    figures.append({
                        "format": "png",
                        "base64": base64.b64encode(buf.getvalue()).decode(),
                    })
                plt.close("all")
            except Exception as e:
                logger.warning(f"Figure capture error: {e}")

    except _TimeoutError as e:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)
        return {
            "success": False, "stdout": stdout_buf.getvalue()[:50000],
            "result": None, "error": str(e), "traceback": None,
            "figures": figures, "execution_time": round(_time.time() - start, 3),
        }
    except Exception as e:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)
        tb = traceback.format_exc()
        return {
            "success": False, "stdout": stdout_buf.getvalue()[:50000],
            "result": None, "error": str(e), "traceback": tb[:5000],
            "figures": figures, "execution_time": round(_time.time() - start, 3),
        }

    stdout_text = stdout_buf.getvalue()
    if len(stdout_text) > 50000:
        stdout_text = stdout_text[:50000] + "\n... (output truncated)"

    result_str = ""
    if result_val is not None:
        try:
            result_str = repr(result_val)
            if len(result_str) > 10000:
                result_str = result_str[:10000] + "..."
        except:
            result_str = "<unrepresentable result>"

    return {
        "success": True, "stdout": stdout_text, "result": result_str,
        "error": None, "traceback": None, "figures": figures,
        "execution_time": round(_time.time() - start, 3),
    }
