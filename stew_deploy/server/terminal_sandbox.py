"""
STEW Terminal Sandbox - Real shell + Python execution for agentic tasks.
Security: allowlist-based command filtering, temp dirs, timeouts, output caps.
Only owner/admin users get terminal access (enforced in tool_agent.py).
"""
import os, re, io, ast, json, base64 as _b64, shutil, tempfile, logging, subprocess, threading, traceback
from contextlib import redirect_stdout, redirect_stderr

logger = logging.getLogger(__name__)
_ALLOWED_COMMANDS = {
    "python3",
    "python",
    "pip",
    "pip3",
    "node",
    "npm",
    "npx",
    "ruby",
    "gem",
    "ls",
    "cat",
    "head",
    "tail",
    "wc",
    "find",
    "tree",
    "file",
    "stat",
    "cp",
    "mv",
    "touch",
    "mkdir",
    "rmdir",
    "du",
    "df",
    "echo",
    "printf",
    "tee",
    "sort",
    "uniq",
    "cut",
    "tr",
    "diff",
    "comm",
    "grep",
    "egrep",
    "fgrep",
    "rg",
    "awk",
    "sed",
    "jq",
    "tar",
    "zip",
    "unzip",
    "gzip",
    "gunzip",
    "curl",
    "wget",
    "ping",
    "host",
    "dig",
    "nslookup",
    "bc",
    "expr",
    "factor",
    "seq",
    "shuf",
    "base64",
    "md5sum",
    "sha256sum",
    "whoami",
    "pwd",
    "env",
    "date",
    "cal",
    "uptime",
    "uname",
    "git",
    "ffmpeg",
    "ffprobe",
    "convert",
    "identify",
    "cargo",
    "go",
    "gcc",
    "g++",
    "make",
    "cmake",
    "rm",
}

_FORBIDDEN_RE = [
    (r"\brm\s+(-rf|--force)\s+/", "rm on root is forbidden"),
    (r"\brm\s+(-rf|--force)\s+\*", "rm wildcard is forbidden"),
    (r"\bmkfs\\b", "disk format tool is forbidden"),
    (r"\bdd\b.*of=/dev/", "dd to device is forbidden"),
    (r"\bshutdown\b", "shutdown is forbidden"),
    (r"\breboot\b", "reboot is forbidden"),
    (r"\bhalt\b", "halt is forbidden"),
    (r"\bkillall\b", "killall is forbidden"),
    (r"\bpkill\b", "pkill is forbidden"),
    (r"\bkill\b\s+-9", "kill -9 is forbidden"),
    (r"\biptables\b", "iptables is forbidden"),
    (r"\bcrontab\b", "crontab is forbidden"),
    (r"\bexport\s+PATH=", "modifying PATH is forbidden"),
    (r"\bsudo\b", "sudo is forbidden"),
    (r"\bsu\b\s", "su is forbidden"),
    (r"\bnohup\b", "nohup is forbidden"),
    (r"\bscreen\b", "screen is forbidden"),
    (r"\btmux\b", "tmux is forbidden"),
    (r"\bchmod\\b.*777.*\\s/", "unsafe perm on system paths"),
    (r"\bchown\b.*\s/", "chown on system paths is forbidden"),
    (r"\bmount\b", "mount is forbidden"),
    (r"\bumount\b", "umount is forbidden"),
    (r"\bnc\b", "netcat is forbidden"),
    (r"\bssh\b", "ssh is forbidden"),
    (r"\bscp\b", "scp is forbidden"),
]

_PYTHON_FORBIDDEN = [
    ("__import__", "Use of __import__ is not allowed"),
    ("__subclasses__", "Access to __subclasses__ is not allowed"),
    ("__bases__", "Access to __bases__ is not allowed"),
    ("__mro__", "Access to __mro__ is not allowed"),
]

def _validate_shell_command(cmd):
    cmd = cmd.strip()
    if not cmd: return False, "Empty command"
    for pattern, msg in _FORBIDDEN_RE:
        if re.search(pattern, cmd, re.IGNORECASE): return False, msg
    parts = re.split(r"\s*[|&]+\s*", cmd)
    for part in parts:
        part = re.sub(r"^\w+=\S+\s+", "", part.strip())
        if not part: continue
        part = re.sub(r"[<>]\s*\S+", "", part).strip()
        if not part: continue
        tokens = part.split()
        if not tokens: continue
        base_cmd = tokens[0].split("/")[-1]
        if base_cmd not in _ALLOWED_COMMANDS:
            return False, "Command " + base_cmd + " is not in the allowed list."
    return True, ""

def _validate_python_code(code):
    for pattern, msg in _PYTHON_FORBIDDEN:
        if pattern in code: return False, msg
    return True, ""

class _TermResult:
    def __init__(self):
        self.success = False; self.stdout = ""; self.stderr = ""
        self.result = None; self.error = None; self.traceback = None
        self.figures = []; self.timed_out = False; self.exit_code = 0
        self.files_created = []

def _run_shell(cmd, workdir, timeout, result):
    try:
        proc = subprocess.run(cmd, shell=True, cwd=workdir, capture_output=True, timeout=timeout, text=True)
        result.success = proc.returncode == 0
        result.stdout = proc.stdout or ""; result.stderr = proc.stderr or ""
        result.exit_code = proc.returncode
    except subprocess.TimeoutExpired:
        result.timed_out = True; result.error = "Timed out (" + str(timeout) + "s)"
    except Exception as e: result.error = str(e)

def _run_python(code, workdir, timeout, result):
    ok, msg = _validate_python_code(code)
    if not ok: result.error = msg; return
    try: tree = ast.parse(code)
    except SyntaxError as e: result.error = "Syntax error: " + str(e); return
    last_expr = None; body = list(tree.body)
    if body and isinstance(body[-1], ast.Expr):
        last_expr = ast.Expression(body=body[-1].value); body = body[:-1]
    exec_code = ast.Module(body=body, type_ignores=[])
    stdout_buf = io.StringIO(); result_val = None
    import builtins as _builtins
    safe_globals = {"__builtins__": dict(vars(_builtins)), "__name__": "__terminal__"}
    try:
        import numpy as np; safe_globals["np"] = np
    except: pass
    try:
        import matplotlib; matplotlib.use("Agg")
        import matplotlib.pyplot as plt; safe_globals["plt"] = plt
    except: pass
    try: import pandas as pd; safe_globals["pd"] = pd
    except: pass
    try: import requests; safe_globals["requests"] = requests
    except: pass
    files_before = set(os.listdir(workdir)) if os.path.exists(workdir) else set()
    try:
        with redirect_stdout(stdout_buf), redirect_stderr(io.StringIO()):
            if body: exec(compile(exec_code, "<terminal>", "exec"), safe_globals)
            if last_expr: result_val = eval(compile(last_expr, "<terminal>", "eval"), safe_globals)
        result.success = True; result.stdout = stdout_buf.getvalue()
        if result_val is not None:
            try:
                result.result = repr(result_val)
                if len(result.result) > 10000: result.result = result.result[:10000] + "..."
            except: result.result = "<unrepresentable>"
        try:
            import matplotlib.pyplot as plt
            for fnum in plt.get_fignums():
                fig = plt.figure(fnum); buf = io.BytesIO()
                fig.savefig(buf, format="png", dpi=100, bbox_inches="tight"); buf.seek(0)
                result.figures.append({"format": "png", "base64": _b64.b64encode(buf.getvalue()).decode()})
            plt.close("all")
        except: pass
        if os.path.exists(workdir):
            result.files_created = sorted(set(os.listdir(workdir)) - files_before)
    except Exception as e:
        result.error = str(e); result.traceback = traceback.format_exc()[:5000]
        result.stdout = stdout_buf.getvalue()

def execute_shell(cmd, timeout=30, workdir=None):
    import time as _time; start = _time.time()
    ok, msg = _validate_shell_command(cmd)
    if not ok:
        return {"success": False, "stdout": "", "stderr": "", "error": msg, "exit_code": -1, "timed_out": False, "execution_time": round(_time.time() - start, 3)}
    if not workdir: workdir = tempfile.mkdtemp(prefix="stew_term_")
    result = _TermResult()
    thread = threading.Thread(target=_run_shell, args=(cmd, workdir, timeout, result), daemon=True)
    thread.start(); thread.join(timeout=timeout + 2)
    if thread.is_alive(): result.timed_out = True; result.success = False; result.error = "Timed out (" + str(timeout) + "s)"
    return {"success": result.success, "stdout": result.stdout[:50000], "stderr": result.stderr[:20000], "error": result.error, "exit_code": result.exit_code, "timed_out": result.timed_out, "execution_time": round(_time.time() - start, 3), "workdir": workdir}

def execute_python(code, timeout=30, workdir=None):
    import time as _time; start = _time.time()
    ok, msg = _validate_python_code(code)
    if not ok:
        return {"success": False, "stdout": "", "result": "", "error": msg, "figures": [], "files_created": [], "files_to_send": [], "timed_out": False, "execution_time": round(_time.time() - start, 3)}
    if not workdir: workdir = tempfile.mkdtemp(prefix="stew_py_")
    result = _TermResult()
    thread = threading.Thread(target=_run_python, args=(code, workdir, timeout, result), daemon=True)
    thread.start(); thread.join(timeout=timeout + 2)
    if thread.is_alive(): result.timed_out = True; result.success = False; result.error = "Timed out (" + str(timeout) + "s)"
    files_to_send = []
    for fname in result.files_created:
        fpath = os.path.join(workdir, fname)
        if os.path.isfile(fpath) and os.path.getsize(fpath) < 20 * 1024 * 1024:
            try:
                with open(fpath, "rb") as f:
                    files_to_send.append({"filename": fname, "base64": _b64.b64encode(f.read()).decode(), "size": os.path.getsize(fpath)})
            except Exception as e: logger.warning("Could not read " + fname + ": " + str(e))
    return {"success": result.success, "stdout": result.stdout[:50000], "result": result.result or "", "error": result.error, "traceback": result.traceback, "figures": result.figures, "files_created": result.files_created, "files_to_send": files_to_send, "timed_out": result.timed_out, "execution_time": round(_time.time() - start, 3), "workdir": workdir}

