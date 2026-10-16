use pyo3::create_exception;
use pyo3::exceptions::{PyException, PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyByteArray, PyBytes, PyDict, PyFloat, PyInt, PyList, PyString, PyTuple};
use rquickjs::loader::{Loader, Resolver};
use rquickjs::module::Declared;
use rquickjs::prelude::Rest;
use rquickjs::{
    context::EvalOptions, convert::Coerced, ArrayBuffer, Atom, CatchResultExt, CaughtError, Ctx,
    Exception, Function, Module, Object, Persistent, Promise, Type, TypedArray, Value,
};
use std::cell::{Cell, RefCell};
use std::collections::HashMap;
use std::rc::Rc;
use std::time::{Duration, Instant};

create_exception!(_jsbox, JavaScriptError, PyException);
create_exception!(_jsbox, JsTimeoutError, PyException);

const MAX_DEPTH: usize = 64;
const MAX_SAFE: f64 = 9007199254740992.0;
const DEFAULT_MEMORY: usize = 64 * 1024 * 1024;
const DEFAULT_STACK: usize = 1024 * 1024;
const DEFAULT_VALUE_BYTES: usize = 16 * 1024 * 1024;
/// Each converted node costs at least this much, so huge arrays of tiny
/// values are bounded too, not just big strings.
const NODE_COST: usize = 16;

type Modules = Rc<RefCell<HashMap<String, String>>>;

/// Bounds how much data one conversion may copy across the boundary. With
/// `lossy`, JS values that have no Python equivalent (functions, symbols,
/// cycles) become a descriptive string instead of an error.
struct Budget {
    left: Cell<usize>,
    lossy: bool,
}

impl Budget {
    fn new(max: usize, lossy: bool) -> Self {
        Self { left: Cell::new(max), lossy }
    }

    fn take(&self, n: usize) -> PyResult<()> {
        let left = self.left.get();
        if n > left {
            return Err(PyValueError::new_err("value exceeds max_value_bytes"));
        }
        self.left.set(left - n);
        Ok(())
    }
}

struct StaticResolver(Modules);
struct StaticLoader(Modules);

impl Resolver for StaticResolver {
    fn resolve<'js>(&mut self, ctx: &Ctx<'js>, _base: &str, name: &str) -> rquickjs::Result<String> {
        if self.0.borrow().contains_key(name) {
            Ok(name.to_string())
        } else {
            Err(Exception::throw_message(ctx, &format!("module not found: {name}")))
        }
    }
}

impl Loader for StaticLoader {
    fn load<'js>(&mut self, ctx: &Ctx<'js>, name: &str) -> rquickjs::Result<Module<'js, Declared>> {
        let src = self.0.borrow().get(name).cloned();
        match src {
            Some(s) => Module::declare(ctx.clone(), name, s),
            None => Err(Exception::throw_message(ctx, &format!("module not found: {name}"))),
        }
    }
}

fn val_err(e: rquickjs::Error) -> PyErr {
    PyValueError::new_err(e.to_string())
}

fn to_js<'js>(ctx: &Ctx<'js>, v: &Bound<'_, PyAny>, depth: usize, b: &Budget) -> PyResult<Value<'js>> {
    if depth > MAX_DEPTH {
        return Err(PyValueError::new_err("value nested too deeply"));
    }
    b.take(NODE_COST)?;
    if v.is_none() {
        return Ok(Value::new_null(ctx.clone()));
    }
    if v.is_instance_of::<PyBool>() {
        return Ok(Value::new_bool(ctx.clone(), v.extract()?));
    }
    if v.is_instance_of::<PyInt>() {
        let n: i64 = v
            .extract()
            .map_err(|_| PyValueError::new_err("int out of i64 range"))?;
        return Ok(if (n as f64).abs() < MAX_SAFE {
            Value::new_number(ctx.clone(), n as f64)
        } else {
            rquickjs::BigInt::from_i64(ctx.clone(), n).map_err(val_err)?.into_value()
        });
    }
    if v.is_instance_of::<PyFloat>() {
        return Ok(Value::new_number(ctx.clone(), v.extract()?));
    }
    if v.is_instance_of::<PyString>() {
        let s: String = v.extract()?;
        b.take(s.len())?;
        return Ok(rquickjs::String::from_str(ctx.clone(), &s).map_err(val_err)?.into_value());
    }
    if v.is_instance_of::<PyBytes>() || v.is_instance_of::<PyByteArray>() {
        let bytes: Vec<u8> = v.extract()?;
        b.take(bytes.len())?;
        return Ok(TypedArray::<u8>::new(ctx.clone(), bytes).map_err(val_err)?.into_value());
    }
    if v.is_instance_of::<PyList>() || v.is_instance_of::<PyTuple>() {
        let arr = rquickjs::Array::new(ctx.clone()).map_err(val_err)?;
        for (i, item) in v.try_iter()?.enumerate() {
            arr.set(i, to_js(ctx, &item?, depth + 1, b)?).map_err(val_err)?;
        }
        return Ok(arr.into_value());
    }
    if let Ok(d) = v.cast::<PyDict>() {
        let obj = Object::new(ctx.clone()).map_err(val_err)?;
        for (k, val) in d.iter() {
            let key: String = k
                .extract()
                .map_err(|_| PyTypeError::new_err("dict keys must be str"))?;
            b.take(key.len())?;
            // An Atom keeps NUL bytes in keys; a plain &str key would truncate at them.
            let atom = Atom::from_str(ctx.clone(), &key).map_err(val_err)?;
            obj.set(atom, to_js(ctx, &val, depth + 1, b)?).map_err(val_err)?;
        }
        return Ok(obj.into_value());
    }
    Err(PyTypeError::new_err(format!(
        "cannot convert {} to JavaScript",
        v.get_type().name()?
    )))
}

fn to_py<'js>(py: Python<'_>, v: &Value<'js>, depth: usize, b: &Budget) -> PyResult<Py<PyAny>> {
    if depth > MAX_DEPTH {
        if b.lossy {
            return Ok("[Circular]".into_pyobject(py)?.into_any().unbind());
        }
        return Err(PyValueError::new_err("value nested too deeply (cycle?)"));
    }
    b.take(NODE_COST)?;
    Ok(match v.type_of() {
        Type::Undefined | Type::Null | Type::Uninitialized => py.None(),
        Type::Bool => v.as_bool().unwrap().into_pyobject(py)?.to_owned().into_any().unbind(),
        Type::Int => v.as_int().unwrap().into_pyobject(py)?.into_any().unbind(),
        Type::Float => {
            let f = v.as_float().unwrap();
            if f.fract() == 0.0 && f.abs() < MAX_SAFE {
                (f as i64).into_pyobject(py)?.into_any().unbind()
            } else {
                f.into_pyobject(py)?.into_any().unbind()
            }
        }
        Type::BigInt => {
            // Stringify and parse: `to_i64` silently wraps modulo 2^64.
            let digits = v.get::<Coerced<String>>().map_err(val_err)?.0;
            b.take(digits.len())?;
            py.get_type::<PyInt>()
                .call1((digits,))
                .map_err(|_| PyValueError::new_err("BigInt too large to convert"))?
                .unbind()
        }
        Type::String => {
            let s = v.as_string().unwrap().to_string().map_err(val_err)?;
            b.take(s.len())?;
            s.into_pyobject(py)?.into_any().unbind()
        }
        Type::Array => {
            let list = PyList::empty(py);
            for item in v.as_array().unwrap().iter::<Value>() {
                list.append(to_py(py, &item.map_err(val_err)?, depth + 1, b)?)?;
            }
            list.into_any().unbind()
        }
        Type::Object | Type::Exception => {
            let obj = v.as_object().unwrap();
            let bytes = TypedArray::<u8>::from_object(obj.clone())
                .ok()
                .and_then(|t| t.as_bytes().map(<[u8]>::to_vec))
                .or_else(|| {
                    ArrayBuffer::from_object(obj.clone())
                        .and_then(|a| a.as_bytes().map(<[u8]>::to_vec))
                });
            if let Some(bytes) = bytes {
                b.take(bytes.len())?;
                return Ok(PyBytes::new(py, &bytes).into_any().unbind());
            }
            let d = PyDict::new(py);
            // Keys come back as Values: reading them as `String` goes through a
            // C string and truncates at an embedded NUL.
            for kv in obj.props::<Value, Value>() {
                let (kv_key, val) = kv.map_err(val_err)?;
                let k = match kv_key.as_string() {
                    Some(s) => s.to_string().map_err(val_err)?,
                    None => kv_key.get::<Coerced<String>>().map_err(val_err)?.0,
                };
                if val.is_function() && !b.lossy {
                    continue;
                }
                b.take(k.len())?;
                d.set_item(k, to_py(py, &val, depth + 1, b)?)?;
            }
            d.into_any().unbind()
        }
        Type::Function | Type::Constructor if b.lossy => "[function]".into_pyobject(py)?.into_any().unbind(),
        Type::Symbol if b.lossy => "[symbol]".into_pyobject(py)?.into_any().unbind(),
        t if b.lossy => format!("[{t:?}]").into_pyobject(py)?.into_any().unbind(),
        t => return Err(PyTypeError::new_err(format!("cannot convert JS {t:?} to Python"))),
    })
}

/// A Python exception raised by a host function, paired with the exact JS
/// error object thrown for it. Only that object is attributed to it, so the
/// guest cannot forge the link with a look-alike message.
type HostErr = Rc<RefCell<Option<(Persistent<Value<'static>>, PyErr)>>>;

/// `Persistent` has no `Drop`: letting one fall out of scope leaks its JS value
/// and QuickJS aborts the process when the runtime is freed. Every stash must
/// be restored (and the restored value dropped) inside the context instead.
fn clear_stash<'js>(ctx: &Ctx<'js>, host_err: &HostErr) {
    let stashed = host_err.borrow_mut().take();
    if let Some((saved, _py_err)) = stashed {
        let _ = saved.restore(ctx);
    }
}

/// The JS error handed to the guest for a failed host function. The guest only
/// learns the exception class, never its message.
fn host_failure<'js>(ctx: &Ctx<'js>, host_err: &HostErr, py: Python<'_>, e: PyErr) -> rquickjs::Result<Value<'js>> {
    let name = e.get_type(py).name().map(|n| n.to_string()).unwrap_or_default();
    let ex = Exception::from_message(ctx.clone(), &format!("host function failed: {name}"))?;
    let val = ex.into_value();
    clear_stash(ctx, host_err);
    *host_err.borrow_mut() = Some((Persistent::save(ctx, val.clone()), e));
    Ok(val)
}

/// A pending awaitable started by an async host function. The persistents are
/// restored in `Asy::release`; never just dropped (see `clear_stash`).
struct Pending {
    id: u64,
    task: Py<PyAny>,
    resolve: Persistent<Function<'static>>,
    reject: Persistent<Function<'static>>,
}

/// State of one `eval_async` run, driven step by step from Python.
#[derive(Default)]
struct Asy {
    active: Cell<bool>,
    next: Cell<u64>,
    pending: RefCell<Vec<Pending>>,
    main: RefCell<Option<Persistent<Value<'static>>>>,
}

impl Asy {
    fn release<'js>(&self, py: Python<'_>, ctx: &Ctx<'js>) {
        self.active.set(false);
        for p in self.pending.take() {
            let _ = p.task.call_method0(py, "cancel");
            let _ = p.resolve.restore(ctx);
            let _ = p.reject.restore(ctx);
        }
        if let Some(m) = self.main.take() {
            let _ = m.restore(ctx);
        }
    }
}

struct HostCtx {
    host_err: HostErr,
    asy: Rc<Asy>,
    max_value_bytes: usize,
    lossy: bool,
}

fn host_function<'js>(ctx: &Ctx<'js>, func: Py<PyAny>, h: Rc<HostCtx>) -> rquickjs::Result<Function<'js>> {
    Function::new(ctx.clone(), move |ctx: Ctx<'js>, args: Rest<Value<'js>>| -> rquickjs::Result<Value<'js>> {
        Python::attach(|py| {
            let call = || -> PyResult<Value<'js>> {
                let a = args
                    .0
                    .iter()
                    .map(|v| to_py(py, v, 0, &Budget::new(h.max_value_bytes, h.lossy)))
                    .collect::<PyResult<Vec<_>>>()?;
                let r = func.call1(py, PyTuple::new(py, a)?)?;
                let r = r.bind(py);
                if r.hasattr("__await__")? {
                    if !h.asy.active.get() {
                        let _ = r.call_method0("close");
                        return Err(PyTypeError::new_err("async host functions need eval_async"));
                    }
                    let task = py.import("asyncio")?.call_method1("ensure_future", (r,))?;
                    let (promise, resolve, reject) = Promise::new(&ctx).map_err(val_err)?;
                    let id = h.asy.next.get() + 1;
                    h.asy.next.set(id);
                    h.asy.pending.borrow_mut().push(Pending {
                        id,
                        task: task.unbind(),
                        resolve: Persistent::save(&ctx, resolve),
                        reject: Persistent::save(&ctx, reject),
                    });
                    return Ok(promise.into_value());
                }
                to_js(&ctx, r, 0, &Budget::new(h.max_value_bytes, false))
            };
            match call() {
                Ok(v) => Ok(v),
                Err(e) => host_failure(&ctx, &h.host_err, py, e).and_then(|v| Err(ctx.throw(v))),
            }
        })
    })
}

const CONSOLE_LEVELS: [&str; 8] = ["log", "info", "debug", "dir", "table", "warn", "error", "trace"];

fn install_console<'js>(ctx: &Ctx<'js>, cb: Option<Py<PyAny>>, max: usize) -> rquickjs::Result<()> {
    let console = Object::new(ctx.clone())?;
    for level in CONSOLE_LEVELS {
        let cb = cb.as_ref().map(|c| Python::attach(|py| c.clone_ref(py)));
        let f = Function::new(ctx.clone(), move |args: Rest<Value<'js>>| -> rquickjs::Result<()> {
            if let Some(cb) = &cb {
                Python::attach(|py| {
                    let a: Vec<Py<PyAny>> = args
                        .0
                        .iter()
                        .map(|v| {
                            to_py(py, v, 0, &Budget::new(max, true))
                                .unwrap_or_else(|_| "[unprintable]".into_pyobject(py).unwrap().into_any().unbind())
                        })
                        .collect();
                    if let Ok(list) = PyList::new(py, a) {
                        // A failing console callback must not become a guest-visible error.
                        let _ = cb.call1(py, (level, list));
                    }
                });
            }
            Ok(())
        })?;
        console.set(level, f)?;
    }
    ctx.globals().set("console", console)
}

fn parse_timeout(timeout: Option<f64>) -> PyResult<Option<Instant>> {
    match timeout {
        None => Ok(None),
        Some(t) if t.is_finite() && t >= 0.0 => Ok(Some(Instant::now() + Duration::from_secs_f64(t.min(1e9)))),
        Some(_) => Err(PyValueError::new_err("timeout must be a finite number >= 0")),
    }
}

#[pyclass(unsendable, subclass)]
struct Runtime {
    ctx: rquickjs::Context,
    deadline: Rc<Cell<Option<Instant>>>,
    total: Cell<Option<Instant>>,
    timed_out: Rc<Cell<bool>>,
    h: Rc<HostCtx>,
    modules: Modules,
}

#[pymethods]
impl Runtime {
    /// `memory_limit`, `max_stack_size` and `max_value_bytes` have safe
    /// defaults; pass 0 to `memory_limit`/`max_stack_size` to disable.
    /// `on_console(level, args)` receives `console.*` calls; `lossy=True`
    /// turns unconvertible JS results (functions, symbols, cycles) into
    /// placeholder strings instead of raising.
    #[new]
    #[pyo3(signature = (memory_limit=DEFAULT_MEMORY, max_stack_size=DEFAULT_STACK, max_value_bytes=DEFAULT_VALUE_BYTES, on_console=None, lossy=false))]
    fn new(
        memory_limit: usize,
        max_stack_size: usize,
        max_value_bytes: usize,
        on_console: Option<Py<PyAny>>,
        lossy: bool,
    ) -> PyResult<Self> {
        let rt = rquickjs::Runtime::new().map_err(val_err)?;
        rt.set_memory_limit(memory_limit);
        rt.set_max_stack_size(max_stack_size);
        let deadline: Rc<Cell<Option<Instant>>> = Rc::default();
        let timed_out: Rc<Cell<bool>> = Rc::default();
        let (d, t) = (deadline.clone(), timed_out.clone());
        rt.set_interrupt_handler(Some(Box::new(move || {
            let hit = d.get().is_some_and(|dl| Instant::now() >= dl);
            if hit {
                t.set(true);
            }
            hit
        })));
        let modules: Modules = Rc::default();
        rt.set_loader(StaticResolver(modules.clone()), StaticLoader(modules.clone()));
        let ctx = rquickjs::Context::full(&rt).map_err(val_err)?;
        ctx.with(|ctx| install_console(&ctx, on_console, max_value_bytes)).map_err(val_err)?;
        Ok(Self {
            ctx,
            deadline,
            total: Cell::new(None),
            timed_out,
            h: Rc::new(HostCtx {
                host_err: Rc::default(),
                asy: Rc::default(),
                max_value_bytes,
                lossy,
            }),
            modules,
        })
    }

    /// Evaluate a script; a returned promise is settled by draining microtasks.
    #[pyo3(signature = (code, timeout=None))]
    fn eval(&self, py: Python<'_>, code: &str, timeout: Option<f64>) -> PyResult<Py<PyAny>> {
        self.run(py, timeout, |ctx| Self::eval_script(ctx, code))
    }

    /// Evaluate a module registered with `add_static_module`; returns its
    /// exports as a dict.
    #[pyo3(signature = (name, timeout=None))]
    fn eval_module(&self, py: Python<'_>, name: &str, timeout: Option<f64>) -> PyResult<Py<PyAny>> {
        let src = self.modules.borrow().get(name).cloned();
        let Some(src) = src else {
            return Err(JavaScriptError::new_err(format!("module not found: {name}")));
        };
        self.run(py, timeout, |ctx| {
            let (m, p) = Module::declare(ctx.clone(), name, src).catch(ctx)?.eval().catch(ctx)?;
            p.finish::<()>().catch(ctx)?;
            Ok(m.namespace().catch(ctx)?.into_value())
        })
    }

    fn add_static_module(&self, name: &str, source: &str) {
        self.modules.borrow_mut().insert(name.to_string(), source.to_string());
    }

    /// Expose a Python callable as a JS global. A callable that returns an
    /// awaitable gives JS a Promise and needs `eval_async`.
    fn bind_function(&self, name: &str, func: Py<PyAny>) -> PyResult<()> {
        self.ctx.with(|ctx| -> PyResult<()> {
            let f = host_function(&ctx, func, self.h.clone()).map_err(val_err)?;
            ctx.globals().set(name, f).map_err(val_err)
        })
    }

    /// Copy a Python value (dict/list/str/number/bytes/None) into a JS global.
    fn bind_object(&self, name: &str, value: &Bound<'_, PyAny>) -> PyResult<()> {
        self.ctx.with(|ctx| {
            let v = to_js(&ctx, value, 0, &Budget::new(self.h.max_value_bytes, false))?;
            ctx.globals().set(name, v).map_err(val_err)
        })
    }

    // --- stepwise driver for `eval_async` (see python/jsbox/__init__.py) ---

    fn _begin_async(&self, py: Python<'_>, code: &str, timeout: Option<f64>) -> PyResult<()> {
        let dl = parse_timeout(timeout)?;
        self.timed_out.set(false);
        self.total.set(dl);
        self.deadline.set(dl);
        self.h.asy.active.set(true);
        let out = self.ctx.with(|ctx| -> PyResult<()> {
            clear_stash(&ctx, &self.h.host_err);
            let v = Self::eval_script(&ctx, code).map_err(|e| self.map_err(&ctx, e))?;
            *self.h.asy.main.borrow_mut() = Some(Persistent::save(&ctx, v));
            Ok(())
        });
        self.deadline.set(None);
        let _ = py;
        out
    }

    /// Returns `(True, result)` when the script's promise settled, else
    /// `(False, [(id, task), ...])` for the host to await.
    fn _poll(&self, py: Python<'_>) -> PyResult<(bool, Py<PyAny>)> {
        self.deadline.set(self.total.get());
        let out = self.ctx.with(|ctx| -> PyResult<(bool, Py<PyAny>)> {
            while !self.timed_out.get() && ctx.execute_pending_job() {}
            let Some(main) = self.h.asy.main.borrow_mut().take() else {
                return Err(JavaScriptError::new_err("eval_async was not started"));
            };
            let v = main.restore(&ctx).map_err(val_err)?;
            let Some(p) = v.clone().into_promise() else {
                return self.convert_out(py, &ctx, &v).map(|r| (true, r));
            };
            match p.result::<Value>() {
                Some(r) => {
                    let v = r.catch(&ctx).map_err(|e| self.map_err(&ctx, e))?;
                    self.convert_out(py, &ctx, &v).map(|r| (true, r))
                }
                None => {
                    if self.timed_out.get() {
                        return Err(JsTimeoutError::new_err("script exceeded its timeout"));
                    }
                    let pending = self.h.asy.pending.borrow();
                    if pending.is_empty() {
                        return Err(JavaScriptError::new_err("promise never settled (no pending jobs)"));
                    }
                    let list = PyList::empty(py);
                    for p in pending.iter() {
                        list.append((p.id, p.task.clone_ref(py)))?;
                    }
                    drop(pending);
                    *self.h.asy.main.borrow_mut() = Some(Persistent::save(&ctx, v));
                    Ok((false, list.into_any().unbind()))
                }
            }
        });
        self.deadline.set(None);
        out
    }

    /// Resolve (`ok`) or reject the JS promise of host call `id`.
    fn _settle(&self, py: Python<'_>, id: u64, ok: bool, value: &Bound<'_, PyAny>) -> PyResult<()> {
        let taken = {
            let mut pending = self.h.asy.pending.borrow_mut();
            pending.iter().position(|p| p.id == id).map(|i| pending.remove(i))
        };
        let Some(p) = taken else { return Ok(()) };
        self.deadline.set(self.total.get());
        let out = self.ctx.with(|ctx| -> PyResult<()> {
            let resolve = p.resolve.restore(&ctx).map_err(val_err)?;
            let reject = p.reject.restore(&ctx).map_err(val_err)?;
            let outcome: Result<Value<'_>, PyErr> = if ok {
                to_js(&ctx, value, 0, &Budget::new(self.h.max_value_bytes, false))
            } else {
                Err(PyErr::from_value(value.clone()))
            };
            match outcome {
                Ok(v) => resolve.call::<_, ()>((v,)).map_err(val_err),
                Err(e) => {
                    let err = host_failure(&ctx, &self.h.host_err, py, e).map_err(val_err)?;
                    reject.call::<_, ()>((err,)).map_err(val_err)
                }
            }
        });
        self.deadline.set(None);
        out
    }

    fn _end_async(&self, py: Python<'_>) {
        self.ctx.with(|ctx| {
            self.h.asy.release(py, &ctx);
            clear_stash(&ctx, &self.h.host_err);
        });
    }
}

impl Drop for Runtime {
    fn drop(&mut self) {
        Python::attach(|py| self.ctx.with(|ctx| {
            self.h.asy.release(py, &ctx);
            clear_stash(&ctx, &self.h.host_err);
        }));
    }
}

impl Runtime {
    /// Scripts run sloppy like a browser/V8 script; rquickjs defaults to strict.
    fn eval_script<'js>(ctx: &Ctx<'js>, code: &str) -> Result<Value<'js>, CaughtError<'js>> {
        let mut o = EvalOptions::default();
        o.strict = false;
        ctx.eval_with_options::<Value, _>(code, o).catch(ctx)
    }

    fn run<'a>(
        &self,
        py: Python<'a>,
        timeout: Option<f64>,
        f: impl for<'js> FnOnce(&Ctx<'js>) -> Result<Value<'js>, CaughtError<'js>>,
    ) -> PyResult<Py<PyAny>> {
        let deadline = parse_timeout(timeout)?;
        self.timed_out.set(false);
        self.deadline.set(deadline);
        let out = self.ctx.with(|ctx| {
            clear_stash(&ctx, &self.h.host_err);
            let r = self.run_in(py, &ctx, f);
            clear_stash(&ctx, &self.h.host_err);
            r
        });
        self.deadline.set(None);
        out
    }

    fn run_in<'js>(
        &self,
        py: Python<'_>,
        ctx: &Ctx<'js>,
        f: impl FnOnce(&Ctx<'js>) -> Result<Value<'js>, CaughtError<'js>>,
    ) -> PyResult<Py<PyAny>> {
        let v = f(ctx).and_then(|v| self.settle(ctx, v)).map_err(|e| self.map_err(ctx, e))?;
        self.convert_out(py, ctx, &v)
    }

    fn convert_out<'js>(&self, py: Python<'_>, ctx: &Ctx<'js>, v: &Value<'js>) -> PyResult<Py<PyAny>> {
        to_py(py, v, 0, &Budget::new(self.h.max_value_bytes, self.h.lossy)).map_err(|e| {
            // A getter hitting the interrupt leaves a pending JS exception; clear it.
            let _ = ctx.catch();
            if self.timed_out.get() {
                JsTimeoutError::new_err("script exceeded its timeout")
            } else {
                e
            }
        })
    }

    /// Drain microtasks until a returned promise settles.
    fn settle<'js>(&self, ctx: &Ctx<'js>, v: Value<'js>) -> Result<Value<'js>, CaughtError<'js>> {
        let Some(p) = v.clone().into_promise() else { return Ok(v) };
        loop {
            if let Some(r) = p.result::<Value>() {
                return r.catch(ctx);
            }
            if !ctx.execute_pending_job() {
                return Err(CaughtError::Error(rquickjs::Error::Unknown));
            }
        }
    }

    fn map_err<'js>(&self, ctx: &Ctx<'js>, e: CaughtError<'js>) -> PyErr {
        if self.timed_out.get() {
            return JsTimeoutError::new_err("script exceeded its timeout");
        }
        let stashed = self
            .h
            .host_err
            .borrow_mut()
            .take()
            .map(|(saved, pe)| (saved.restore(ctx).ok(), pe));
        let thrown = match &e {
            CaughtError::Exception(x) => Some(x.clone().into_value()),
            CaughtError::Value(v) => Some(v.clone()),
            CaughtError::Error(_) => None,
        };
        if let (Some((Some(saved), pe)), Some(thrown)) = (stashed, thrown) {
            if saved == thrown {
                return pe;
            }
        }
        let (name, message, stack) = match &e {
            CaughtError::Exception(x) => (
                x.as_object().get::<_, Option<String>>("name").ok().flatten(),
                x.message().unwrap_or_default(),
                x.stack().unwrap_or_default(),
            ),
            CaughtError::Value(v) => (None, format!("{v:?}"), String::new()),
            CaughtError::Error(rquickjs::Error::Unknown) => {
                (None, "promise never settled (no pending jobs)".to_string(), String::new())
            }
            CaughtError::Error(e) => (None, e.to_string(), String::new()),
        };
        let text = if stack.is_empty() { message.clone() } else { format!("{message}\n{stack}") };
        let err = JavaScriptError::new_err(text);
        Python::attach(|py| {
            let v = err.value(py);
            let _ = v.setattr("name", name);
            let _ = v.setattr("message", message);
            let _ = v.setattr("stack", stack);
        });
        err
    }
}

#[pymodule]
fn _jsbox(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Runtime>()?;
    m.add("JavaScriptError", m.py().get_type::<JavaScriptError>())?;
    m.add("JsTimeoutError", m.py().get_type::<JsTimeoutError>())?;
    Ok(())
}
