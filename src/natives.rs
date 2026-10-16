//! Pure, ambient-authority-free web/Node globals that bundles feature-detect.
//!
//! `Buffer`: JSZip (and so pptxgenjs) probes `typeof Buffer` and, when it is
//! there, UTF-8-encodes entry names and text through `Buffer.from(str, 'utf-8')`
//! instead of two interpreted per-character loops. That was ~30% of a pptxgenjs
//! `write()` under QuickJS. This is a small, spec-faithful subset of Node's
//! `Buffer` (a `Uint8Array` subclass): no I/O, no timers, no allocation outside
//! the QuickJS heap, so `memory_limit` already bounds everything it creates.
//! Only the two string<->bytes conversions that need a native loop cross into
//! Rust; base64/hex use the engine's own `Uint8Array.prototype.toBase64` etc.
use rquickjs::{Ctx, Function, TypedArray};

const BUFFER_JS: &str = r#"(function (utf8enc, utf8dec, latin1dec) {
  const U8 = Uint8Array;
  const ENC = { utf8: 'utf8', 'utf-8': 'utf8', hex: 'hex', base64: 'base64', base64url: 'base64url',
    latin1: 'latin1', binary: 'latin1', ascii: 'latin1' };
  const norm = (e) => {
    const k = ENC[e === undefined || e === null ? 'utf8' : String(e).toLowerCase()];
    if (k === undefined) throw new TypeError('Unknown encoding: ' + e);
    return k;
  };
  const fromString = (s, enc) => {
    switch (norm(enc)) {
      case 'utf8': return utf8enc(s);
      case 'hex': {
        const n = s.length >> 1; let i = 0;
        // Node stops at the first invalid pair instead of throwing.
        while (i < n && /^[0-9a-fA-F]{2}$/.test(s.substr(i * 2, 2))) i++;
        return U8.fromHex(s.slice(0, i * 2));
      }
      case 'base64': case 'base64url': {
        // Node accepts both alphabets, padded or not, and skips junk.
        const t = s.replace(/-/g, '+').replace(/_/g, '/').replace(/[^A-Za-z0-9+/]/g, '');
        return U8.fromBase64(t, { lastChunkHandling: 'loose' });
      }
      default: { const o = new U8(s.length); for (let i = 0; i < s.length; i++) o[i] = s.charCodeAt(i) & 255; return o; }
    }
  };
  class Buffer extends U8 {
    static from(v, a, b) {
      let u;
      if (typeof v === 'string') u = fromString(v, a);
      else if (v instanceof ArrayBuffer || (typeof SharedArrayBuffer !== 'undefined' && v instanceof SharedArrayBuffer))
        return new Buffer(v, a === undefined ? 0 : a, b === undefined ? v.byteLength - (a || 0) : b);
      else if (v !== null && typeof v === 'object' && v.type === 'Buffer' && Array.isArray(v.data)) u = U8.from(v.data);
      else if (v !== null && typeof v === 'object') u = U8.from(v);
      else throw new TypeError('The first argument must be of type string or an instance of Buffer, ArrayBuffer, or Array or an Array-like Object.');
      return new Buffer(u.buffer, u.byteOffset, u.length);
    }
    static alloc(n, fill, enc) {
      if (typeof n !== 'number' || !(n >= 0)) throw new RangeError('The value of "size" is out of range.');
      const b = new Buffer(n);
      if (fill !== undefined && fill !== 0 && n > 0) b.fill(fill, 0, n, enc);
      return b;
    }
    static allocUnsafe(n) { return Buffer.alloc(n); }
    static allocUnsafeSlow(n) { return Buffer.alloc(n); }
    static isBuffer(b) { return b instanceof Buffer; }
    static isEncoding(e) { return typeof e === 'string' && ENC[e.toLowerCase()] !== undefined; }
    static byteLength(v, enc) {
      return typeof v === 'string' ? fromString(v, enc).length : v.byteLength;
    }
    static concat(list, total) {
      if (!Array.isArray(list)) throw new TypeError('"list" argument must be an Array of Buffers');
      if (total === undefined) { total = 0; for (const x of list) total += x.length; }
      const out = Buffer.alloc(total);
      let p = 0;
      for (const x of list) {
        if (p >= total) break;
        const part = x.length > total - p ? x.subarray(0, total - p) : x;
        out.set(part, p); p += part.length;
      }
      return out;
    }
    static compare(a, b) { return a.compare(b); }
    toString(enc, start = 0, end = this.length) {
      const k = norm(enc);
      start = Math.max(0, start | 0); end = Math.min(this.length, end === undefined ? this.length : end | 0);
      const v = start === 0 && end === this.length ? this : this.subarray(start, end);
      if (end <= start) return '';
      switch (k) {
        case 'utf8': return utf8dec(v);
        case 'hex': return v.toHex();
        case 'base64': return v.toBase64();
        case 'base64url': return v.toBase64({ alphabet: 'base64url', omitPadding: true });
        default: return latin1dec(v);
      }
    }
    toJSON() { return { type: 'Buffer', data: Array.from(this) }; }
    slice(s, e) { return this.subarray(s, e); }
    equals(o) {
      if (!(o instanceof U8)) throw new TypeError('The "otherBuffer" argument must be an instance of Buffer or Uint8Array.');
      if (o.length !== this.length) return false;
      for (let i = 0; i < this.length; i++) if (this[i] !== o[i]) return false;
      return true;
    }
    compare(o) {
      const n = Math.min(this.length, o.length);
      for (let i = 0; i < n; i++) if (this[i] !== o[i]) return this[i] < o[i] ? -1 : 1;
      return this.length < o.length ? -1 : this.length > o.length ? 1 : 0;
    }
    copy(target, ts = 0, ss = 0, se = this.length) {
      const part = this.subarray(ss, se);
      const n = Math.max(0, Math.min(part.length, target.length - ts));
      target.set(part.subarray(0, n), ts);
      return n;
    }
    write(str, off = 0, len, enc) {
      if (typeof len === 'string') { enc = len; len = undefined; }
      const src = fromString(str, enc);
      const n = Math.min(src.length, this.length - off, len === undefined ? Infinity : len);
      this.set(src.subarray(0, n), off);
      return n;
    }
    fill(v, s = 0, e = this.length, enc) {
      if (typeof v === 'string') {
        const src = fromString(v, typeof s === 'string' ? s : enc);
        if (typeof s === 'string') { s = 0; }
        if (src.length === 0) return super.fill(0, s, e);
        for (let i = s, j = 0; i < e; i++, j = (j + 1) % src.length) this[i] = src[j];
        return this;
      }
      return super.fill(v, s, e);
    }
    indexOf(v, from = 0, enc) {
      if (typeof v === 'number') return super.indexOf(v & 255, from);
      const n = typeof v === 'string' ? fromString(v, enc) : v;
      if (from < 0) from = Math.max(0, this.length + from);
      outer: for (let i = from; i <= this.length - n.length; i++) {
        for (let j = 0; j < n.length; j++) if (this[i + j] !== n[j]) continue outer;
        return i;
      }
      return -1;
    }
    includes(v, from, enc) { return this.indexOf(v, from, enc) !== -1; }
  }
  // readUInt8/16/32 (+LE/BE), readInt*, writeUInt*, writeInt* via DataView.
  const view = (b) => new DataView(b.buffer, b.byteOffset, b.byteLength);
  const check = (b, off, n) => {
    if (typeof off !== 'number' || off < 0 || off + n > b.length || off !== Math.floor(off))
      throw new RangeError('The value of "offset" is out of range.');
  };
  for (const [bits, name] of [[8, ''], [16, ''], [32, '']]) {
    const n = bits / 8;
    for (const signed of [false, true]) {
      const t = (signed ? 'Int' : 'Uint') + bits;
      for (const le of n === 1 ? [''] : ['LE', 'BE']) {
        const rn = 'read' + (signed ? 'Int' : 'UInt') + bits + le, wn = 'write' + (signed ? 'Int' : 'UInt') + bits + le;
        Buffer.prototype[rn] = function (off = 0) { check(this, off, n); return view(this)['get' + t](off, le === 'LE'); };
        Buffer.prototype[wn] = function (v, off = 0) {
          check(this, off, n);
          const lo = signed ? -(2 ** (bits - 1)) : 0, hi = signed ? 2 ** (bits - 1) - 1 : 2 ** bits - 1;
          if (typeof v !== 'number' || v < lo || v > hi) throw new RangeError('The value of "value" is out of range.');
          view(this)['set' + t](off, v, le === 'LE'); return off + n;
        };
      }
    }
  }
  Buffer.poolSize = 8192;
  Object.defineProperty(globalThis, 'Buffer', { value: Buffer, writable: true, configurable: true, enumerable: false });
})"#;

/// `buffer` opts in to the Node `Buffer` global; off by default so a guest that
/// probes `typeof Buffer` still sees a non-Node environment.
pub fn install<'js>(ctx: &Ctx<'js>, buffer: bool) -> rquickjs::Result<()> {
    if !buffer {
        return Ok(());
    }
    let utf8enc = Function::new(ctx.clone(), |ctx: Ctx<'js>, s: rquickjs::String<'js>| -> rquickjs::Result<TypedArray<'js, u8>> {
        TypedArray::new(ctx, s.to_string()?.into_bytes())
    })?;
    // Node's Buffer#toString keeps a leading BOM (TextDecoder strips it).
    let utf8dec = Function::new(ctx.clone(), |ctx: Ctx<'js>, b: TypedArray<'js, u8>| -> rquickjs::Result<rquickjs::String<'js>> {
        let bytes = unsafe { b.as_bytes() }.unwrap_or_default();
        rquickjs::String::from_str(ctx, &String::from_utf8_lossy(bytes))
    })?;
    let latin1dec = Function::new(ctx.clone(), |ctx: Ctx<'js>, b: TypedArray<'js, u8>| -> rquickjs::Result<rquickjs::String<'js>> {
        let bytes = unsafe { b.as_bytes() }.unwrap_or_default();
        rquickjs::String::from_str(ctx, &bytes.iter().map(|&c| char::from(c)).collect::<String>())
    })?;
    let install: Function = ctx.eval(BUFFER_JS)?;
    install.call::<_, ()>((utf8enc, utf8dec, latin1dec))
}
