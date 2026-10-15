use pyo3::create_exception;
use pyo3::exceptions::{PyException, PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyByteArray, PyBytes, PyDict, PyFloat, PyInt, PyList, PyString, PyTuple};
use rquickjs::loader::{Loader, Resolver};
use rquickjs::prelude::Rest;
use rquickjs::{
    ArrayBuffer, CatchResultExt, CaughtError, Ctx, Exception, Function, Module, Object,
    Type, TypedArray, Value, module::Declared,
};
use std::cell::{Cell, RefCell};
use std::collections::HashMap;
use std::rc::Rc;
use std::time::{Duration, Instant};

create_exception!(_jsbox, JavaScriptError, PyException);
create_exception!(_jsbox, JsTimeoutError, PyException);

const MAX_DEPTH: usize = 64;
const MAX_SAFE: f64 = 9007199254740992.0;

type Modules = Rc<RefCell<HashMap<String, String>>>;

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

fn to_js<'js>(ctx: &Ctx<'js>, v: &Bound<'_, PyAny>, depth: usize) -> PyResult<Value<'js>> {
    if depth > MAX_DEPTH {
        return Err(PyValueError::new_err("value nested too deeply"));
    }
    let js = |e: rquickjs::Error| PyValueError::new_err(e.to_string());
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
            rquickjs::BigInt::from_i64(ctx.clone(), n).map_err(js)?.into_value()
        });
    }
    if v.is_instance_of::<PyFloat>() {
        return Ok(Value::new_number(ctx.clone(), v.extract()?));
    }
    if v.is_instance_of::<PyString>() {
        let s: String = v.extract()?;
        return Ok(rquickjs::String::from_str(ctx.clone(), &s).map_err(js)?.into_value());
    }
    if v.is_instance_of::<PyBytes>() || v.is_instance_of::<PyByteArray>() {
        let b: Vec<u8> = v.extract()?;
        return Ok(TypedArray::<u8>::new(ctx.clone(), b).map_err(js)?.into_value());
    }
    if v.is_instance_of::<PyList>() || v.is_instance_of::<PyTuple>() {
        let arr = rquickjs::Array::new(ctx.clone()).map_err(js)?;
        for (i, item) in v.try_iter()?.enumerate() {
            arr.set(i, to_js(ctx, &item?, depth + 1)?).map_err(js)?;
        }
        return Ok(arr.into_value());
    }
    if let Ok(d) = v.cast::<PyDict>() {
        let obj = Object::new(ctx.clone()).map_err(js)?;
        for (k, val) in d.iter() {
            let key: String = k
                .extract()
                .map_err(|_| PyTypeError::new_err("dict keys must be str"))?;
            obj.set(key, to_js(ctx, &val, depth + 1)?).map_err(js)?;
        }
        return Ok(obj.into_value());
    }
    Err(PyTypeError::new_err(format!(
        "cannot convert {} to JavaScript",
        v.get_type().name()?
    )))
}

fn to_py<'js>(py: Python<'_>, v: &Value<'js>, depth: usize) -> PyResult<Py<PyAny>> {
    if depth > MAX_DEPTH {
        return Err(PyValueError::new_err("value nested too deeply (cycle?)"));
    }
    let js = |e: rquickjs::Error| PyValueError::new_err(e.to_string());
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
        Type::BigInt => v
            .as_big_int()
            .unwrap()
            .clone()
            .to_i64()
            .map_err(js)?
            .into_pyobject(py)?
            .into_any()
            .unbind(),
        Type::String => v
            .as_string()
            .unwrap()
            .to_string()
            .map_err(js)?
            .into_pyobject(py)?
            .into_any()
            .unbind(),
        Type::Array => {
            let list = PyList::empty(py);
            for item in v.as_array().unwrap().iter::<Value>() {
                list.append(to_py(py, &item.map_err(js)?, depth + 1)?)?;
            }
            list.into_any().unbind()
        }
        Type::Object | Type::Exception => {
            let obj = v.as_object().unwrap();
            if let Some(b) = TypedArray::<u8>::from_object(obj.clone())
                .ok()
                .and_then(|t| t.as_bytes().map(|b| b.to_vec()))
            {
                return Ok(PyBytes::new(py, &b).into_any().unbind());
            }
            if let Some(b) = ArrayBuffer::from_object(obj.clone()).and_then(|a| a.as_bytes().map(|b| b.to_vec())) {
                return Ok(PyBytes::new(py, &b).into_any().unbind());
            }
            let d = PyDict::new(py);
            for kv in obj.props::<String, Value>() {
                let (k, val) = kv.map_err(js)?;
                if val.is_function() {
                    continue;
                }
                d.set_item(k, to_py(py, &val, depth + 1)?)?;
            }
            d.into_any().unbind()
        }
        t => return Err(PyTypeError::new_err(format!("cannot convert JS {t:?} to Python"))),
    })
}

fn host_function<'js>(
    ctx: &Ctx<'js>,
    func: Py<PyAny>,
    host_err: Rc<RefCell<Option<PyErr>>>,
) -> rquickjs::Result<Function<'js>> {
    Function::new(ctx.clone(), move |ctx: Ctx<'js>, args: Rest<Value<'js>>| -> rquickjs::Result<Value<'js>> {
        Python::attach(|py| {
            let call = || -> PyResult<Value<'js>> {
                let a = args.0.iter().map(|v| to_py(py, v, 0)).collect::<PyResult<Vec<_>>>()?;
                let r = func.call1(py, PyTuple::new(py, a)?)?;
                if r.bind(py).hasattr("__await__")? {
                    return Err(PyTypeError::new_err("async host functions are not supported yet"));
                }
                to_js(&ctx, r.bind(py), 0)
            };
            call().map_err(|e| {
                let msg = e.to_string();
                *host_err.borrow_mut() = Some(e);
                Exception::throw_message(&ctx, &msg)
            })
        })
    })
}

#[pyclass(unsendable)]
struct Runtime {
    ctx: rquickjs::Context,
    deadline: Rc<Cell<Option<Instant>>>,
    timed_out: Rc<Cell<bool>>,
    host_err: Rc<RefCell<Option<PyErr>>>,
    modules: Modules,
}

#[pymethods]
impl Runtime {
    #[new]
    #[pyo3(signature = (memory_limit=None, max_stack_size=None))]
    fn new(memory_limit: Option<usize>, max_stack_size: Option<usize>) -> PyResult<Self> {
        let err = |e: rquickjs::Error| PyValueError::new_err(e.to_string());
        let rt = rquickjs::Runtime::new().map_err(err)?;
        if let Some(m) = memory_limit {
            rt.set_memory_limit(m);
        }
        if let Some(s) = max_stack_size {
            rt.set_max_stack_size(s);
        }
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
        let ctx = rquickjs::Context::full(&rt).map_err(err)?;
        Ok(Self { ctx, deadline, timed_out, host_err: Rc::default(), modules })
    }

    /// Evaluate a script; promises are settled by draining microtasks.
    #[pyo3(signature = (code, timeout=None))]
    fn eval(&self, py: Python<'_>, code: &str, timeout: Option<f64>) -> PyResult<Py<PyAny>> {
        self.run(py, timeout, |ctx| ctx.eval::<Value, _>(code).catch(ctx))
    }

    /// Evaluate an ES module by name (registered via `add_static_module`) and
    /// return its exports as a dict.
    #[pyo3(signature = (name, timeout=None))]
    fn eval_module(&self, py: Python<'_>, name: &str, timeout: Option<f64>) -> PyResult<Py<PyAny>> {
        let entry = format!("import * as ns from {name:?}; globalThis.__jsbox_ns = ns;");
        self.run(py, timeout, |ctx| {
            let p = Module::evaluate(ctx.clone(), "__jsbox_entry__", entry).catch(ctx)?;
            p.finish::<()>().catch(ctx)?;
            let ns: Value = ctx.globals().get("__jsbox_ns").catch(ctx)?;
            ctx.globals().remove("__jsbox_ns").catch(ctx)?;
            Ok(ns)
        })
    }

    fn add_static_module(&self, name: &str, source: &str) {
        self.modules.borrow_mut().insert(name.to_string(), source.to_string());
    }

    /// Expose a Python callable as a JS global. Sync callables only.
    fn bind_function(&self, name: &str, func: Py<PyAny>) -> PyResult<()> {
        let host_err = self.host_err.clone();
        self.ctx.with(|ctx| -> PyResult<()> {
            let f = host_function(&ctx, func, host_err)
                .map_err(|e| PyValueError::new_err(e.to_string()))?;
            ctx.globals().set(name, f).map_err(|e| PyValueError::new_err(e.to_string()))
        })
    }

    /// Copy a Python value (dict/list/str/number/bytes/None) into a JS global.
    fn bind_object(&self, name: &str, value: &Bound<'_, PyAny>) -> PyResult<()> {
        self.ctx.with(|ctx| {
            let v = to_js(&ctx, value, 0)?;
            ctx.globals().set(name, v).map_err(|e| PyValueError::new_err(e.to_string()))
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
        self.timed_out.set(false);
        *self.host_err.borrow_mut() = None;
        self.deadline.set(timeout.map(|t| Instant::now() + Duration::from_secs_f64(t)));
        let out = self.ctx.with(|ctx| {
            let v = f(&ctx).and_then(|v| self.settle(&ctx, v)).map_err(|e| self.map_err(e))?;
            to_py(py, &v, 0)
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

    fn map_err(&self, e: CaughtError<'_>) -> PyErr {
        if self.timed_out.get() {
            return JsTimeoutError::new_err("script exceeded its timeout");
        }
        if let Some(pe) = self.host_err.borrow_mut().take() {
            return pe;
        }
        match e {
            CaughtError::Exception(x) => JavaScriptError::new_err(format!(
                "{}\n{}",
                x.message().unwrap_or_default(),
                x.stack().unwrap_or_default()
            )),
            CaughtError::Value(v) => JavaScriptError::new_err(format!("{v:?}")),
            CaughtError::Error(rquickjs::Error::Unknown) => {
                JavaScriptError::new_err("promise never settled (no pending jobs)")
            }
            CaughtError::Error(e) => JavaScriptError::new_err(e.to_string()),
        }
    }
}

#[pymodule]
fn _jsbox(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Runtime>()?;
    m.add("JavaScriptError", m.py().get_type::<JavaScriptError>())?;
    m.add("JsTimeoutError", m.py().get_type::<JsTimeoutError>())?;
    Ok(())
}
