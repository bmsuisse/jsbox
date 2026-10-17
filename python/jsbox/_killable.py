"""Run a script in a child process that is killed at the deadline."""

import multiprocessing as mp

from jsbox._jsbox import JavaScriptError, JsTimeoutError


def _child(conn, code, timeout, runtime_kwargs):
    try:
        from jsbox import Runtime

        conn.send(("ok", Runtime(**runtime_kwargs).eval(code, timeout=timeout)))
    except JsTimeoutError as e:
        conn.send(("timeout", str(e)))
    except JavaScriptError as e:
        conn.send(("js", (str(e), e.name, e.message, e.stack)))
    except BaseException as e:  # ValueError/TypeError from conversion, ...
        conn.send(("py", (type(e).__name__, str(e))))
    finally:
        conn.close()


def run_killable(code, *, timeout, grace=1.0, **runtime_kwargs):
    """Evaluate ``code`` in a fresh child process and kill it ``grace`` seconds after ``timeout``.

    ``timeout=`` alone cannot interrupt one long native operation (``BigInt`` powers, huge
    ``normalize``...); this can, because the whole process dies. Costs a process spawn
    (~50-100 ms) and supports pure computation only: no host functions or modules, since those
    cannot cross the process boundary. ``runtime_kwargs`` go to ``Runtime`` (``memory_limit``...).
    Raises ``JsTimeoutError`` when the child had to be killed, and the usual errors otherwise.
    """
    ctx = mp.get_context("spawn")
    recv, send = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_child, args=(send, code, timeout, runtime_kwargs), daemon=True)
    proc.start()
    send.close()
    try:
        if not recv.poll(timeout + grace):
            raise JsTimeoutError("script exceeded its timeout and its process was killed")
        try:
            kind, payload = recv.recv()
        except EOFError:
            raise RuntimeError(f"jsbox worker died (exit code {proc.exitcode})") from None
    finally:
        if proc.is_alive():
            proc.kill()
        proc.join()
        recv.close()
    if kind == "ok":
        return payload
    if kind == "timeout":
        raise JsTimeoutError(payload)
    if kind == "js":
        text, name, message, stack = payload
        err = JavaScriptError(text)
        err.name, err.message, err.stack = name, message, stack
        raise err
    name, text = payload
    raise {"ValueError": ValueError, "TypeError": TypeError, "MemoryError": MemoryError}.get(name, RuntimeError)(text)
