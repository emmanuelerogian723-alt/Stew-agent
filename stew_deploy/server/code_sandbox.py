"""
S.T.E.W Code Execution Sandbox — Safe Python execution for agentic tasks.

Inspired by Kimi's code execution: the LLM writes Python code, we run it
in a restricted sandbox with no network, no file system access, limited
built-ins, and a strict timeout. Output is returned to the LLM.
"""
import io
import ast
import base64
import logging
import threading
import traceback
from contextlib import redirect_stdout, redirect_stderr
from typing import Any

logger = logging.getLogger(__name__)

ALLOWED_MODULES = {
    "math", "json", "re", "datetime", "statistics", "collections",
    "itertools", "random", "string", "textwrap", "decimal", "fractions",
    "hashlib", "unicodedata", "operator", "functools", "bisect",
    "copy", "pprint", "csv", "io",
}

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
    def __init__(self, allowed, optional):
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
                raise ImportError(f"Module '{name}' is not available")
        raise ImportError(
            f"Module '{name}' is not allowed in the S.T.E.W sandbox. "
            f"Allowed: {', '.join(sorted(self.allowed))}"
        )


def _validate_code(code):
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


class _ExecResult:
    """Container for thread execution results."""
    def __init__(self):
        self.success = False
        self.stdout = ""
        self.result = None
        self.error = None
        self.traceback = None
        self.figures = []
        self.timed_out = False


def _run_in_thread(code, timeout, result_container):
    """Actually run the code in a separate thread."""
    import time as _time

    # Validate
    ok, msg = _validate_code(code)
    if not ok:
        result_container.error = msg
        return

    # Parse
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        result_container.error = f"Syntax error: {e}"
        return

    last_expr = None
    body = list(tree.body)
    if body and isinstance(body[-1], ast.Expr):
        last_expr = ast.Expression(body=body[-1].value)
        body = body[:-1]

    exec_code = ast.Module(body=body, type_ignores=[])

    stdout_buf = io.StringIO()
    result_val = None

    safe_globals = {
        "__builtins__": {
            **SAFE_BUILTINS,
            "__import__": _SafeImporter(ALLOWED_MODULES, OPTIONAL_MODULES),
        },
        "__name__": "__sandbox__",
    }

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
        with redirect_stdout(stdout_buf), redirect_stderr(io.StringIO()):
            if body:
                compiled_body = compile(exec_code, "<sandbox>", "exec")
                exec(compiled_body, safe_globals)

            if last_expr:
                compiled_expr = compile(last_expr, "<sandbox>", "eval")
                result_val = eval(compiled_expr, safe_globals)

        result_container.success = True
        result_container.stdout = stdout_buf.getvalue()

        if result_val is not None:
            try:
                result_container.result = repr(result_val)
                if len(result_container.result) > 10000:
                    result_container.result = result_container.result[:10000] + "..."
            except:
                result_container.result = "<unrepresentable>"

        # Capture figures
        if OPTIONAL_MODULES.get("matplotlib"):
            try:
                import matplotlib.pyplot as plt
                fig_nums = plt.get_fignums()
                for fnum in fig_nums:
                    fig = plt.figure(fnum)
                    buf = io.BytesIO()
                    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
                    buf.seek(0)
                    result_container.figures.append({
                        "format": "png",
                        "base64": base64.b64encode(buf.getvalue()).decode(),
                    })
                plt.close("all")
            except Exception as e:
                logger.warning(f"Figure capture error: {e}")

    except Exception as e:
        result_container.error = str(e)
        result_container.traceback = traceback.format_exc()[:5000]
        result_container.stdout = stdout_buf.getvalue()


def execute_code(code, timeout=10):
    """Execute Python code in a restricted sandbox with thread-based timeout."""
    import time as _time
    start = _time.time()

    container = _ExecResult()
    thread = threading.Thread(
        target=_run_in_thread,
        args=(code, timeout, container),
        daemon=True,
    )
    thread.start()
    thread.join(timeout=timeout)

    if thread.is_alive():
        # Thread is still running — timed out
        container.timed_out = True
        container.success = False
        container.error = f"Code execution timed out ({timeout} second limit)"
        # Can't kill the thread, but it's a daemon so it won't block shutdown

    stdout_text = container.stdout
    if len(stdout_text) > 50000:
        stdout_text = stdout_text[:50000] + "\n... (output truncated)"

    return {
        "success": container.success,
        "stdout": stdout_text,
        "result": container.result or "",
        "error": container.error,
        "traceback": container.traceback,
        "figures": container.figures,
        "execution_time": round(_time.time() - start, 3),
    }
