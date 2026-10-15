use pyo3::create_exception;
use pyo3::exceptions::{PyException, PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyByteArray, PyBytes, PyDict, PyFloat, PyInt, PyList, PyString, PyTuple};
use rquickjs::loader::{Loader, Resolver};
use rquickjs::module::Declared;
use rquickjs::prelude::Rest;
use rquickjs::{
    context::EvalOptions, convert::Coerced, ArrayBuffer, Atom, CatchResultExt, CaughtError, Ctx,
    Exception, Function, Module, Object, Persistent, Type, TypedArray, Value,
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

/// Bounds how much data one conversion may copy across the boundary.
struct Budget(Cell<usize>);

impl Budget {
    fn take(&self, n: usize) -> PyResult<()> {
        let left = self.0.get();
        if n > left {
            return Err(PyValueError::new_err("value exceeds max_value_bytes"));
        }
        self.0.set(left - n);
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
                if val.is_function() {
                    continue;
                }
                b.take(k.len())?;
                d.set_item(k, to_py(py, &val, depth + 1, b)?)?;
            }
            d.into_any().unbind()
        }
        t => return Err(PyTypeError::new_err(format!("cannot convert JS {t:?} to Python"))),
    })
}

/// A Python exception raised by a host function, paired with the exact JS
/// error object thrown for it. Only that object is attributed to it, so the
/// guest cannot forge the link with a look-alike message.
type HostErr = Rc<RefCell<Option<(Persistent<Value<'static>>, PyErr)>>>;

fn host_function<'js>(
    ctx: &Ctx<'js>,
    func: Py<PyAny>,
    host_err: HostErr,
    max_value_bytes: usize,
) -> rquickjs::Result<Function<'js>> {
    Function::new(ctx.clone(), move |ctx: Ctx<'js>, args: Rest<Value<'js>>| -> rquickjs::Result<Value<'js>> {
        Python::attach(|py| {
            let budget = Budget(Cell::new(max_value_bytes));
            let call = || -> PyResult<Value<'js>> {
                let a = args
                    .0
                    .iter()
                    .map(|v| to_py(py, v, 0, &budget))
                    .collect::<PyResult<Vec<_>>>()?;
                let r = func.call1(py, PyTuple::new(py, a)?)?;
                if r.bind(py).hasattr("__await__")? {
                    return Err(PyTypeError::new_err("async host functions are not supported yet"));
                }
                to_js(&ctx, r.bind(py), 0, &Budget(Cell::new(max_value_bytes)))
            };
            call().map_err(|e| {
                // The guest only learns the exception class, never its message.
                let name = e.get_type(py).name().map(|n| n.to_string()).unwrap_or_default();
                let msg = format!("host function failed: {name}");
                match Exception::from_message(ctx.clone(), &msg) {
                    Ok(ex) => {
                        let val = ex.into_value();
                        *host_err.borrow_mut() = Some((Persistent::save(&ctx, val.clone()), e));
                        ctx.throw(val)
                    }
                    Err(err) => err,
                }
            })
        })
    })
}

#[pyclass(unsendable)]
struct Runtime {
    ctx: rquickjs::Context,
    deadline: Rc<Cell<Option<Instant>>>,
    timed_out: Rc<Cell<bool>>,
    host_err: HostErr,
    modules: Modules,
    max_value_bytes: usize,
}

#[pymethods]
impl Runtime {
    /// `memory_limit`, `max_stack_size` and `max_value_bytes` have safe
    /// defaults; pass 0 to `memory_limit`/`max_stack_size` to disable.
    #[new]
    #[pyo3(signature = (memory_limit=DEFAULT_MEMORY, max_stack_size=DEFAULT_STACK, max_value_bytes=DEFAULT_VALUE_BYTES))]
    fn new(memory_limit: usize, max_stack_size: usize, max_value_bytes: usize) -> PyResult<Self> {
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
        Ok(Self {
            ctx,
            deadline,
            timed_out,
            host_err: Rc::default(),
            modules,
            max_value_bytes,
        })
    }

    /// Evaluate a script; a returned promise is settled by draining microtasks.
    #[pyo3(signature = (code, timeout=None))]
    fn eval(&self, py: Python<'_>, code: &str, timeout: Option<f64>) -> PyResult<Py<PyAny>> {
        self.run(py, timeout, |ctx| {
            // Scripts run sloppy like a browser/V8 script; rquickjs defaults to strict.
            let mut o = EvalOptions::default();
            o.strict = false;
            ctx.eval_with_options::<Value, _>(code, o).catch(ctx)
        })
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

    /// Expose a Python callable as a JS global. Sync callables only.
    fn bind_function(&self, name: &str, func: Py<PyAny>) -> PyResult<()> {
        self.ctx.with(|ctx| -> PyResult<()> {
            let f = host_function(&ctx, func, self.host_err.clone(), self.max_value_bytes)
                .map_err(val_err)?;
            ctx.globals().set(name, f).map_err(val_err)
        })
    }

    /// Copy a Python value (dict/list/str/number/bytes/None) into a JS global.
    fn bind_object(&self, name: &str, value: &Bound<'_, PyAny>) -> PyResult<()> {
        self.ctx.with(|ctx| {
            let v = to_js(&ctx, value, 0, &Budget(Cell::new(self.max_value_bytes)))?;
            ctx.globals().set(name, v).map_err(val_err)
        })
    }
}

impl Runtime {
    fn run<'a>(
        &self,
        py: Python<'a>,
        timeout: Option<f64>,
        f: impl for<'js> FnOnce(&Ctx<'js>) -> Result<Value<'js>, CaughtError<'js>>,
    ) -> PyResult<Py<PyAny>> {
        let deadline = match timeout {
            None => None,
            Some(t) if t.is_finite() && t >= 0.0 => Some(Instant::now() + Duration::from_secs_f64(t.min(1e9))),
            Some(_) => return Err(PyValueError::new_err("timeout must be a finite number >= 0")),
        };
        self.timed_out.set(false);
        *self.host_err.borrow_mut() = None;
        self.deadline.set(deadline);
        let out = self.ctx.with(|ctx| {
            let v = f(&ctx).and_then(|v| self.settle(&ctx, v)).map_err(|e| self.map_err(&ctx, e))?;
            to_py(py, &v, 0, &Budget(Cell::new(self.max_value_bytes))).map_err(|e| {
                // A getter hitting the interrupt leaves a pending JS exception; clear it.
                let _ = ctx.catch();
                if self.timed_out.get() {
                    JsTimeoutError::new_err("script exceeded its timeout")
                } else {
                    e
                }
            })
        });
        self.deadline.set(None);
        out
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
        let stashed = self.host_err.borrow_mut().take();
        let thrown = match &e {
            CaughtError::Exception(x) => Some(x.clone().into_value()),
            CaughtError::Value(v) => Some(v.clone()),
            CaughtError::Error(_) => None,
        };
        if let (Some((saved, pe)), Some(thrown)) = (stashed, thrown) {
            if saved.restore(ctx).is_ok_and(|v| v == thrown) {
                return pe;
            }
        }
        JavaScriptError::new_err(match &e {
            CaughtError::Exception(x) => {
                format!("{}\n{}", x.message().unwrap_or_default(), x.stack().unwrap_or_default())
            }
            CaughtError::Value(v) => format!("{v:?}"),
            CaughtError::Error(rquickjs::Error::Unknown) => {
                "promise never settled (no pending jobs)".to_string()
            }
            CaughtError::Error(e) => e.to_string(),
        })
    }
}

#[pymodule]
fn _jsbox(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Runtime>()?;
    m.add("JavaScriptError", m.py().get_type::<JavaScriptError>())?;
    m.add("JsTimeoutError", m.py().get_type::<JsTimeoutError>())?;
    Ok(())
}
