"""
Implementations for every symbol glslc.elf imports (dynamically undefined
symbols it expects a "host" to provide). On real build machines
these are supplied by their own in-house tool; here they're supplied by
plain Python.

Each handler has the signature `handler(emu) -> None`. It reads whatever
arguments it needs directly out of registers/guest-memory via the `emu`
helpers, does its job, sets a return value if the real function isn't
void, and finally transfers control back with `emu.return_to_caller()`
(or `emu.return_to(saved_lr)` if it made a nested guest call in between --
see qsort).

A few notes on things that are *assumptions* rather than certainties,
since none of this could be executed/tested in the environment that wrote
it (no way to install/run Unicorn there) -- if compilation misbehaves,
these are the first places to look, and running with debug=True will show
every stub call with its arguments so you can compare against what you'd
expect:

  * NvOsFopen/NvOsFclose/NvOsFread/NvOsFwrite/NvOsFseek/NvOsFtell:
    NVIDIA's NvOs layer signatures aren't in the header we have, so the
    argument order here is my best reconstruction of the well-known public
    NvOs API shape (NvError-returning, handle-out-param style). Your
    example doesn't use #include paths or on-disk dumps, so these likely
    never even get called for it.
  * __nnmusl_init_dso: stubbed as a no-op. Its real job (registering the
    module's TLS/relocation info with author's musl-based runtime) is
    made moot by the fact that *we* already did all the relocation work
    ourselves before any guest code ran.
  * printf-family: format-string handling is "best effort" (covers
    %d/%i/%u/%x/%X/%o/%c/%s/%p/%f/%g/%e with width/precision), which is
    fine because these only affect console/log text, never the actual
    compiled shader output.
"""
import math
import re
import struct
import sys

from . import emu_core


# ---------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------
def _safe(f, *a):
    try:
        r = f(*a)
        if isinstance(r, complex):
            return float('nan')
        return r
    except (ValueError, OverflowError, ZeroDivisionError):
        return float('nan')


def _digit_val(ch):
    if ch.isdigit():
        return int(ch)
    if 'a' <= ch.lower() <= 'z':
        return ord(ch.lower()) - ord('a') + 10
    return 99


class ArgReader:
    """Pulls consecutive varargs straight out of X/D registers. Correct for
    the common case (a handful of int/pointer/string args) since AAPCS64
    guarantees sub-word ints are pre-extended to full registers; doesn't
    handle the rare case of >8 integer varargs (stack-spilled)."""
    def __init__(self, emu, next_x, next_d=0):
        self.emu = emu
        self.xi = next_x
        self.di = next_d

    def next_int(self):
        if self.xi > 7:
            return 0
        v = self.emu.get_arg(self.xi)
        self.xi += 1
        return v

    def next_float(self):
        if self.di > 7:
            return 0.0
        v = self.emu.get_farg(self.di)
        self.di += 1
        return v


class VaListReader:
    """Reads a real AAPCS64 va_list struct out of guest memory:
    { void *stack; void *gr_top; void *vr_top; int32 gr_offs; int32 vr_offs; }
    """
    def __init__(self, emu, valist_ptr):
        self.emu = emu
        self.p = valist_ptr

    def next_int(self):
        emu = self.emu
        offs = emu.read_i32(self.p + 24)
        if offs < 0:
            gr_top = emu.read_u64(self.p + 8)
            addr = (gr_top + offs) & 0xFFFFFFFFFFFFFFFF
            val = emu.read_u64(addr)
            emu.write_bytes(self.p + 24, struct.pack('<i', offs + 8))
            return val
        stack = emu.read_u64(self.p + 0)
        val = emu.read_u64(stack)
        emu.write_u64(self.p + 0, stack + 8)
        return val

    def next_float(self):
        emu = self.emu
        offs = emu.read_i32(self.p + 28)
        if offs < 0:
            vr_top = emu.read_u64(self.p + 16)
            addr = (vr_top + offs) & 0xFFFFFFFFFFFFFFFF
            raw = emu.read_u64(addr)
            emu.write_bytes(self.p + 28, struct.pack('<i', offs + 16))
        else:
            stack = emu.read_u64(self.p + 0)
            raw = emu.read_u64(stack)
            emu.write_u64(self.p + 0, stack + 8)
        return struct.unpack('<d', struct.pack('<Q', raw))[0]


def _format(emu, fmt, args):
    """Best-effort printf-style formatter. `args` has .next_int()/.next_float()."""
    out = []
    i, n = 0, len(fmt)
    while i < n:
        c = fmt[i]
        if c != '%':
            out.append(c)
            i += 1
            continue
        j = i + 1
        flags = ''
        while j < n and fmt[j] in '-+ 0#':
            flags += fmt[j]; j += 1

        # width: literal digits, or '*' meaning "read an int arg for this"
        width = ''
        if j < n and fmt[j] == '*':
            w = args.next_int() & 0xFFFFFFFF
            if w & 0x80000000:
                w -= 1 << 32
            if w < 0:
                flags += '-' if '-' not in flags else ''
                w = -w
            width = str(w)
            j += 1
        else:
            while j < n and fmt[j].isdigit():
                width += fmt[j]; j += 1

        # precision: '.' + literal digits, or '.' + '*' meaning "read an int arg"
        precision = None
        if j < n and fmt[j] == '.':
            j += 1
            if j < n and fmt[j] == '*':
                p = args.next_int() & 0xFFFFFFFF
                if p & 0x80000000:
                    p -= 1 << 32
                precision = p  # negative -> treated as "no precision" below, per C99
                j += 1
            else:
                pd = ''
                while j < n and fmt[j].isdigit():
                    pd += fmt[j]; j += 1
                precision = int(pd) if pd else 0

        length_mod = ''
        while j < n and fmt[j] in 'lhzjtL':
            length_mod += fmt[j]; j += 1

        if j >= n:
            out.append(fmt[i:])
            break
        conv = fmt[j]
        j += 1

        # Bit width for integer conversions: a bare int (no length modifier,
        # or 'h'/'hh') is 32-bit; 'l'/'ll'/'z'/'j'/'t' are the 64-bit ones on
        # this LP64 AArch64 ABI. Sign-extending everything from bit 63
        # regardless of this (the old behavior) is why a real -1 (32-bit,
        # living in a register as 0x00000000FFFFFFFF) printed as 4294967295
        # instead of -1.
        bits = 64 if length_mod in ('l', 'll', 'z', 'j', 't', 'L') else 32
        mask = (1 << bits) - 1
        sign_bit = 1 << (bits - 1)

        spec = '%' + flags + width
        if precision is not None and precision >= 0:
            spec += '.' + str(precision)

        try:
            if conv == '%':
                out.append('%')
            elif conv in 'di':
                v = args.next_int() & mask
                if v & sign_bit:
                    v -= 1 << bits
                out.append((spec + 'd') % v)
            elif conv == 'u':
                out.append((spec + 'd') % (args.next_int() & mask))
            elif conv in 'xX':
                out.append((spec + conv) % (args.next_int() & mask))
            elif conv == 'o':
                out.append((spec + 'o') % (args.next_int() & mask))
            elif conv == 'c':
                out.append(chr(args.next_int() & 0xFF))
            elif conv == 's':
                ptr = args.next_int()
                if not ptr:
                    s = '(null)'
                elif precision is not None and precision >= 0:
                    # %.*s / %.Ns is allowed to read a non-NUL-terminated
                    # string -- read exactly `precision` bytes rather than
                    # scanning for a NUL that may not be there.
                    s = emu.read_bytes(ptr, precision).decode('utf-8', errors='replace')
                else:
                    s = emu.read_cstr(ptr).decode('utf-8', errors='replace')
                out.append(((('%' + flags + width) + 's') % s))
            elif conv == 'p':
                out.append(f'0x{args.next_int():x}')
            elif conv in 'fFgGeE':
                out.append((spec + conv) % args.next_float())
            else:
                out.append(spec + length_mod + conv)
        except Exception:
            out.append(spec + length_mod + conv)
        i = j
    return ''.join(out)


# ---------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------
def register_all(emu):
    for name, fn in _HANDLERS.items():
        emu.register_stub(name, fn)


# ---- mem/string ----
def h_memcpy(emu):
    dst, src, nb = emu.get_arg(0), emu.get_arg(1), emu.get_arg(2)
    if nb:
        emu.write_bytes(dst, emu.read_bytes(src, nb))
    emu.set_return(dst)
    emu.return_to_caller()


def h_memset(emu):
    dst, val, nb = emu.get_arg(0), emu.get_arg(1) & 0xFF, emu.get_arg(2)
    if nb:
        emu.write_bytes(dst, bytes([val]) * nb)
    emu.set_return(dst)
    emu.return_to_caller()


def h_memcmp(emu):
    a, b, nb = emu.get_arg(0), emu.get_arg(1), emu.get_arg(2)
    da, db = emu.read_bytes(a, nb), emu.read_bytes(b, nb)
    emu.set_return((da > db) - (da < db))
    emu.return_to_caller()


def h_strlen(emu):
    emu.set_return(len(emu.read_cstr(emu.get_arg(0))))
    emu.return_to_caller()


def h_strcpy(emu):
    dst, src = emu.get_arg(0), emu.get_arg(1)
    emu.write_bytes(dst, emu.read_cstr(src) + b'\x00')
    emu.set_return(dst)
    emu.return_to_caller()


def h_strncpy(emu):
    dst, src, nb = emu.get_arg(0), emu.get_arg(1), emu.get_arg(2)
    data = emu.read_cstr(src)
    out = data[:nb] if len(data) >= nb else data + b'\x00' * (nb - len(data))
    emu.write_bytes(dst, out)
    emu.set_return(dst)
    emu.return_to_caller()


def h_strcat(emu):
    dst, src = emu.get_arg(0), emu.get_arg(1)
    dstr = emu.read_cstr(dst)
    emu.write_bytes(dst + len(dstr), emu.read_cstr(src) + b'\x00')
    emu.set_return(dst)
    emu.return_to_caller()


def h_strncat(emu):
    dst, src, nb = emu.get_arg(0), emu.get_arg(1), emu.get_arg(2)
    dstr = emu.read_cstr(dst)
    emu.write_bytes(dst + len(dstr), emu.read_cstr(src)[:nb] + b'\x00')
    emu.set_return(dst)
    emu.return_to_caller()


def h_strcmp(emu):
    a, b = emu.read_cstr(emu.get_arg(0)), emu.read_cstr(emu.get_arg(1))
    emu.set_return((a > b) - (a < b))
    emu.return_to_caller()


def h_strncmp(emu):
    nb = emu.get_arg(2)
    a, b = emu.read_cstr(emu.get_arg(0))[:nb], emu.read_cstr(emu.get_arg(1))[:nb]
    emu.set_return((a > b) - (a < b))
    emu.return_to_caller()


def h_strcasecmp(emu):
    a = emu.read_cstr(emu.get_arg(0)).lower()
    b = emu.read_cstr(emu.get_arg(1)).lower()
    emu.set_return((a > b) - (a < b))
    emu.return_to_caller()


def h_strchr(emu):
    s, c = emu.get_arg(0), emu.get_arg(1) & 0xFF
    data = emu.read_cstr(s)
    if c == 0:
        emu.set_return(s + len(data))
    else:
        idx = data.find(bytes([c]))
        emu.set_return(s + idx if idx != -1 else 0)
    emu.return_to_caller()


def h_strrchr(emu):
    s, c = emu.get_arg(0), emu.get_arg(1) & 0xFF
    data = emu.read_cstr(s)
    if c == 0:
        emu.set_return(s + len(data))
    else:
        idx = data.rfind(bytes([c]))
        emu.set_return(s + idx if idx != -1 else 0)
    emu.return_to_caller()


def h_strstr(emu):
    hay_addr = emu.get_arg(0)
    hay, needle = emu.read_cstr(hay_addr), emu.read_cstr(emu.get_arg(1))
    idx = hay.find(needle)
    emu.set_return(hay_addr + idx if idx != -1 else 0)
    emu.return_to_caller()


def h_strtok(emu):
    s = emu.get_arg(0)
    delims = emu.read_cstr(emu.get_arg(1))
    dset = set(delims)
    if s:
        emu._strtok_buf = bytearray(emu.read_cstr(s))
        emu._strtok_addr = s
        emu._strtok_pos = 0
    elif getattr(emu, '_strtok_buf', None) is None:
        emu.set_return(0)
        emu.return_to_caller()
        return
    buf, pos, nb = emu._strtok_buf, emu._strtok_pos, len(emu._strtok_buf)
    while pos < nb and buf[pos] in dset:
        pos += 1
    if pos >= nb:
        emu._strtok_buf = None
        emu.set_return(0)
        emu.return_to_caller()
        return
    start = pos
    while pos < nb and buf[pos] not in dset:
        pos += 1
    token_addr = emu._strtok_addr + start
    if pos < nb:
        emu.write_u8(emu._strtok_addr + pos, 0)
        pos += 1
    emu._strtok_pos = pos
    emu.set_return(token_addr)
    emu.return_to_caller()


def h_memmove(emu):
    h_memcpy(emu)  # python-side read-then-write already snapshots correctly


# ---- ctype ----
def h_tolower(emu):
    c = emu.get_arg(0) & 0xFF
    emu.set_return(c + 32 if 65 <= c <= 90 else c)
    emu.return_to_caller()


def h_toupper(emu):
    c = emu.get_arg(0) & 0xFF
    emu.set_return(c - 32 if 97 <= c <= 122 else c)
    emu.return_to_caller()


def h_isalnum(emu):
    c = emu.get_arg(0) & 0xFF
    emu.set_return(1 if chr(c).isalnum() and c < 128 else 0)
    emu.return_to_caller()


def h_isalpha(emu):
    c = emu.get_arg(0) & 0xFF
    emu.set_return(1 if chr(c).isalpha() and c < 128 else 0)
    emu.return_to_caller()


def h_isspace(emu):
    c = emu.get_arg(0) & 0xFF
    emu.set_return(1 if chr(c) in ' \t\n\r\v\f' else 0)
    emu.return_to_caller()


# ---- stdlib ----
def h_atoi(emu):
    s = emu.read_cstr(emu.get_arg(0)).decode('latin1', errors='replace').strip()
    m = re.match(r'[+-]?\d+', s)
    emu.set_return(int(m.group()) if m else 0)
    emu.return_to_caller()


def h_atof(emu):
    s = emu.read_cstr(emu.get_arg(0)).decode('latin1', errors='replace').strip()
    m = re.match(r'[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?', s)
    emu.set_freturn(float(m.group()) if m else 0.0)
    emu.return_to_caller()


def h_strtol(emu):
    s_ptr, endptr, base = emu.get_arg(0), emu.get_arg(1), emu.get_arg(2)
    text = emu.read_cstr(s_ptr, max_len=512).decode('latin1', errors='replace')
    i, n = 0, len(text)
    while i < n and text[i] in ' \t\n\r\v\f':
        i += 1
    sign = 1
    if i < n and text[i] in '+-':
        sign = -1 if text[i] == '-' else 1
        i += 1
    b = base
    if b == 0:
        if text[i:i + 2].lower() == '0x':
            b, i = 16, i + 2
        elif i < n and text[i] == '0':
            b = 8
        else:
            b = 10
    elif b == 16 and text[i:i + 2].lower() == '0x':
        i += 2
    start = i
    val = 0
    while i < n and _digit_val(text[i]) < b:
        val = val * b + _digit_val(text[i])
        i += 1
    val *= sign
    if endptr:
        emu.write_u64(endptr, s_ptr if i == start else s_ptr + i)
    emu.set_return(val)
    emu.return_to_caller()


def h_qsort(emu):
    base, nmemb, size, compar = emu.get_arg(0), emu.get_arg(1), emu.get_arg(2), emu.get_arg(3)
    saved_lr = emu.get_lr()  # nested guest calls below will clobber X30
    if nmemb > 1 and size > 0:
        import functools
        elems = [emu.read_bytes(base + i * size, size) for i in range(nmemb)]
        scratch = emu.heap.alloc(size * 2)
        a_addr, b_addr = scratch, scratch + size

        def cmp(x, y):
            emu.write_bytes(a_addr, x)
            emu.write_bytes(b_addr, y)
            r = emu.call_guest_function(compar, [a_addr, b_addr]) & 0xFFFFFFFF
            if r & 0x80000000:
                r -= 0x100000000
            return r

        elems.sort(key=functools.cmp_to_key(cmp))
        emu.write_bytes(base, b''.join(elems))
    emu.set_return(0)
    emu.return_to(saved_lr)


# ---- math ----
def h_sqrt(emu):
    emu.set_freturn(_safe(math.sqrt, emu.get_farg(0)))
    emu.return_to_caller()


def h_sin(emu):
    emu.set_freturn(_safe(math.sin, emu.get_farg(0)))
    emu.return_to_caller()


def h_cos(emu):
    emu.set_freturn(_safe(math.cos, emu.get_farg(0)))
    emu.return_to_caller()


def h_tan(emu):
    emu.set_freturn(_safe(math.tan, emu.get_farg(0)))
    emu.return_to_caller()


def h_asin(emu):
    emu.set_freturn(_safe(math.asin, emu.get_farg(0)))
    emu.return_to_caller()


def h_acos(emu):
    emu.set_freturn(_safe(math.acos, emu.get_farg(0)))
    emu.return_to_caller()


def h_atan(emu):
    emu.set_freturn(_safe(math.atan, emu.get_farg(0)))
    emu.return_to_caller()


def h_atan2(emu):
    y, x = emu.get_farg(0), emu.get_farg(1)
    emu.set_freturn(_safe(math.atan2, y, x))
    emu.return_to_caller()


def h_tanh(emu):
    emu.set_freturn(_safe(math.tanh, emu.get_farg(0)))
    emu.return_to_caller()


def h_exp(emu):
    emu.set_freturn(_safe(math.exp, emu.get_farg(0)))
    emu.return_to_caller()


def h_exp2(emu):
    emu.set_freturn(_safe(lambda v: 2.0 ** v, emu.get_farg(0)))
    emu.return_to_caller()


def h_exp2f(emu):
    emu.set_freturn32(_safe(lambda v: 2.0 ** v, emu.get_farg32(0)))
    emu.return_to_caller()


def h_log(emu):
    emu.set_freturn(_safe(math.log, emu.get_farg(0)))
    emu.return_to_caller()


def h_logf(emu):
    emu.set_freturn32(_safe(math.log, emu.get_farg32(0)))
    emu.return_to_caller()


def h_pow(emu):
    x, y = emu.get_farg(0), emu.get_farg(1)
    emu.set_freturn(_safe(math.pow, x, y))
    emu.return_to_caller()


def h_fmod(emu):
    x, y = emu.get_farg(0), emu.get_farg(1)
    emu.set_freturn(_safe(math.fmod, x, y))
    emu.return_to_caller()


def h_ldexp(emu):
    x, e = emu.get_farg(0), emu.get_arg(0)  # int arg has its own (GP) register slot
    if e & 0x80000000:
        e -= 0x100000000
    emu.set_freturn(_safe(math.ldexp, x, e))
    emu.return_to_caller()


def h_finite(emu):
    v = emu.get_farg(0)
    emu.set_return(1 if math.isfinite(v) else 0)
    emu.return_to_caller()


def h_expf(emu):
    emu.set_freturn32(_safe(math.exp, emu.get_farg32(0)))
    emu.return_to_caller()


def h_frexp(emu):
    x, exp_ptr = emu.get_farg(0), emu.get_arg(0)
    m, e = _safe(math.frexp, x), 0
    if isinstance(m, tuple):
        m, e = m
    if exp_ptr:
        emu.write_u32(exp_ptr, e & 0xFFFFFFFF)
    emu.set_freturn(m if not isinstance(m, tuple) else 0.0)
    emu.return_to_caller()


# ---- errno / env / exit ----
def h_errno_location(emu):
    emu.set_return(emu.errno_addr)
    emu.return_to_caller()


def h_getenv(emu):
    emu.set_return(0)  # clean environment: nothing set
    emu.return_to_caller()


def h_exit(emu):
    raise emu_core.GuestExit(emu.get_arg(0))


def h_clock(emu):
    # Only ever used (as far as we've seen) for elapsed-time bookkeeping/
    # logging, never for anything that affects the compiled output, so any
    # monotonically-increasing value in a plausible CLOCKS_PER_SEC==1e6
    # ("microseconds") range is fine. time.process_time() is monotonic
    # within one run, which is all a single compile invocation ever needs.
    import time
    emu.set_return(int(time.process_time() * 1_000_000) & 0xFFFFFFFFFFFFFFFF)
    emu.return_to_caller()


# ---- setjmp/longjmp (implemented via Unicorn context save/restore) ----
def h_setjmp(emu):
    env = emu.get_arg(0)
    emu.jmpbufs[env] = emu.uc.context_save()
    emu.set_return(0)
    emu.return_to_caller()


def h_longjmp(emu):
    env, val = emu.get_arg(0), emu.get_arg(1)
    ctx = emu.jmpbufs.get(env)
    if ctx is None:
        # Nothing sane to do with an unknown jmp_buf; just return to caller.
        emu.set_return(0)
        emu.return_to_caller()
        return
    emu.uc.context_restore(ctx)
    ret_addr = emu.uc.reg_read(emu_core.UC_ARM64_REG_X30)  # restored to the setjmp-call-site LR
    emu.set_return(1 if val == 0 else val)
    emu.return_to(ret_addr)


# ---- C++ operator new/delete ----
def h_Znwm(emu):
    size = emu.get_arg(0)
    emu.set_return(emu.heap.alloc(max(1, size)))
    emu.return_to_caller()


def h_ZdlPv(emu):
    emu.heap.free(emu.get_arg(0))
    emu.return_to_caller()


# ---- glslc's own allocator hooks ----
def h_glslc_Alloc(emu):
    # x1 is NOT a real alignment argument for this import -- confirmed by
    # disassembly of the actual call sites (e.g. module+0x410's calloc-style
    # helper only ever sets x0 before branching straight into glslc_Alloc's
    # PLT stub; x1/x2/x3 are just whatever was left over from unrelated
    # code). Treating x1 as alignment let garbage values through and blew
    # up the heap allocator. Just honor size, with normal malloc-style
    # alignment.
    size = emu.get_arg(0)
    emu.set_return(emu.heap.alloc(max(1, size), align=16))
    emu.return_to_caller()


def h_glslc_Realloc(emu):
    ptr, size = emu.get_arg(0), emu.get_arg(1)
    old_size = emu.heap.size_of(ptr)
    new_addr = emu.heap.alloc(max(1, size))
    if ptr and old_size:
        emu.write_bytes(new_addr, emu.read_bytes(ptr, min(old_size, size)))
    if ptr:
        emu.heap.free(ptr)
    emu.set_return(new_addr)
    emu.return_to_caller()


def h_glslc_Free(emu):
    emu.heap.free(emu.get_arg(0))
    emu.return_to_caller()


def h_glslc_GetAllocator(emu):
    # Confirmed via disassembly: glslcSetAllocator(allocate, free,
    # reallocate, userPtr) calls straight through to this, forwarding the
    # same four args, and expects back a pointer to a writable buffer --
    # glslcSetAllocator itself then writes a small internal adapter vtable
    # (a handful of pointer-sized fields) into that memory. We don't need
    # to replicate glslc's internal struct layout, just hand back real,
    # zeroed, generously-sized memory instead of NULL.
    addr = emu.heap.alloc(128)
    emu.write_bytes(addr, b'\x00' * 128)
    emu.set_return(addr)
    emu.return_to_caller()


# ---- Itanium C++ ABI guard variables (thread-safe function-local statics).
#      Only the low byte of the guard word is the real "already initialized"
#      flag on every AArch64 target that uses the *generic* Itanium ABI
#      (as opposed to 32-bit ARM's separate __aeabi guard scheme, which
#      doesn't apply here). We're single-threaded, so no locking is needed
#      -- acquire just needs to tell the guest "go ahead and run the
#      initializer" the first time, and "skip it" every time after. ----
def h_cxa_guard_acquire(emu):
    guard = emu.get_arg(0)
    done = getattr(emu, '_cxa_guards_done', None)
    if done is None:
        done = emu._cxa_guards_done = set()
    if guard in done or emu.read_u8(guard) != 0:
        emu.set_return(0)   # already initialized -- skip the initializer
    else:
        emu.set_return(1)   # not initialized -- caller will run it, then call guard_release
    emu.return_to_caller()


def h_cxa_guard_release(emu):
    guard = emu.get_arg(0)
    done = getattr(emu, '_cxa_guards_done', None)
    if done is None:
        done = emu._cxa_guards_done = set()
    done.add(guard)
    emu.write_u8(guard, 1)
    emu.return_to_caller()


def h_cxa_guard_abort(emu):
    # Initializer threw partway through -- leave the guard clear so a later
    # attempt (if any) tries again, matching real __cxa_guard_abort.
    emu.return_to_caller()


# ---- pthread mutex/cond: the emulator is single-threaded (one Unicorn
#      context, nothing ever runs concurrently with the guest), so these
#      degrade to no-ops -- lock/unlock always "succeed" instantly, and
#      broadcast/wait need no real synchronization since nothing else is
#      running to race with. A real pthread_cond_wait blocks until signaled,
#      which we can't honor if it's ever reached for a reason other than
#      "immediately followed by the signal on this same thread", but no
#      call site that would deadlock has shown up in testing. ----
def h_pthread_mutex_lock(emu):
    emu.set_return(0)
    emu.return_to_caller()


h_pthread_mutex_unlock = h_pthread_mutex_lock


def h_pthread_cond_wait(emu):
    emu.set_return(0)
    emu.return_to_caller()


h_pthread_cond_broadcast = h_pthread_cond_wait


# ---- locale: this codebase already treats all text as raw bytes (UTF-8 in
#      practice) everywhere else, so rather than modeling real locale
#      objects we always behave as the "C" locale and hand back a distinct
#      non-NULL opaque handle for any locale_t a caller creates. The *_l
#      suffixed functions (strtod_l, isdigit_l, etc.) can then just ignore
#      their trailing locale_t arg and call straight through to the
#      already-implemented non-'_l' behavior. ----
_C_LOCALE = 0x1  # any fixed non-zero sentinel; never dereferenced as a real object
_LC_GLOBAL_LOCALE = 0xFFFFFFFFFFFFFFFF  # matches musl's ((locale_t)-1)


def h_newlocale(emu):
    emu.set_return(_C_LOCALE)
    emu.return_to_caller()


def h_freelocale(emu):
    emu.return_to_caller()


def h_uselocale(emu):
    newloc = emu.get_arg(0)
    old = getattr(emu, '_current_locale', _LC_GLOBAL_LOCALE)
    if newloc != 0:
        emu._current_locale = newloc
    emu.set_return(old)
    emu.return_to_caller()


def h_ctype_get_mb_cur_max(emu):
    emu.set_return(1)  # "C" locale: multibyte encoding is 1 byte per char
    emu.return_to_caller()


def h_mbtowc(emu):
    # "C" locale multibyte encoding is the identity mapping (1 byte == 1
    # wide char), so this is just "read a byte, zero-extend it".
    pwc, s, n = emu.get_arg(0), emu.get_arg(1), emu.get_arg(2)
    if s == 0:
        emu.set_return(0)  # stateless encoding -> no shift state to reset
        emu.return_to_caller()
        return
    if n == 0:
        emu.set_return(-1 & 0xFFFFFFFFFFFFFFFF)
        emu.return_to_caller()
        return
    b = emu.read_u8(s)
    if pwc:
        emu.write_u32(pwc, b)
    emu.set_return(0 if b == 0 else 1)
    emu.return_to_caller()


# ---- Nintendo's relocation-read-only-protection hook. We map everything
#      RWX for simplicity (this is a single trusted local file, not a
#      security sandbox -- see emu_core.py), so there's nothing for this to
#      actually do. ----
def h_nn_ro_ProtectRelro(emu):
    emu.set_return(0)
    emu.return_to_caller()


# ---- Author's NvOs internal-module init hook ----
def h_nnmusl_init_dso(emu):
    emu.set_return(0)
    emu.return_to_caller()


# ---- stdio ----
def h_printf(emu):
    text = _format(emu, emu.read_cstr(emu.get_arg(0)).decode('utf-8', errors='replace'),
                    ArgReader(emu, next_x=1, next_d=0))
    sys.stdout.write(text)
    emu.set_return(len(text))
    emu.return_to_caller()


def h_puts(emu):
    text = emu.read_cstr(emu.get_arg(0)).decode('utf-8', errors='replace')
    print(text)
    emu.set_return(len(text) + 1)
    emu.return_to_caller()


def h_fputs(emu):
    text = emu.read_cstr(emu.get_arg(0)).decode('utf-8', errors='replace')
    sys.stdout.write(text)
    emu.set_return(1)
    emu.return_to_caller()


def h_fputc(emu):
    c = emu.get_arg(0) & 0xFF
    sys.stdout.write(chr(c))
    emu.set_return(c)
    emu.return_to_caller()


h_putc = h_fputc  # same signature/behaviour for our purposes


def h_fflush(emu):
    sys.stdout.flush()
    emu.set_return(0)
    emu.return_to_caller()


def h_fprintf(emu):
    stream = emu.get_arg(0)
    text = _format(emu, emu.read_cstr(emu.get_arg(1)).decode('utf-8', errors='replace'),
                    ArgReader(emu, next_x=2, next_d=0))
    f = emu.open_files.get(stream)
    if f is not None:
        f.write(text.encode('utf-8', errors='replace'))
    else:
        sys.stdout.write(text)
    emu.set_return(len(text))
    emu.return_to_caller()


def h_vfprintf(emu):
    stream = emu.get_arg(0)
    text = _format(emu, emu.read_cstr(emu.get_arg(1)).decode('utf-8', errors='replace'),
                    VaListReader(emu, emu.get_arg(2)))
    f = emu.open_files.get(stream)
    if f is not None:
        f.write(text.encode('utf-8', errors='replace'))
    else:
        sys.stdout.write(text)
    emu.set_return(len(text))
    emu.return_to_caller()


def h_sprintf(emu):
    buf = emu.get_arg(0)
    text = _format(emu, emu.read_cstr(emu.get_arg(1)).decode('utf-8', errors='replace'),
                    ArgReader(emu, next_x=2, next_d=0))
    data = text.encode('utf-8', errors='replace')
    emu.write_bytes(buf, data + b'\x00')
    emu.set_return(len(data))
    emu.return_to_caller()


def h_vsprintf(emu):
    buf = emu.get_arg(0)
    text = _format(emu, emu.read_cstr(emu.get_arg(1)).decode('utf-8', errors='replace'),
                    VaListReader(emu, emu.get_arg(2)))
    data = text.encode('utf-8', errors='replace')
    emu.write_bytes(buf, data + b'\x00')
    emu.set_return(len(data))
    emu.return_to_caller()


def h_snprintf(emu):
    buf, size = emu.get_arg(0), emu.get_arg(1)
    text = _format(emu, emu.read_cstr(emu.get_arg(2)).decode('utf-8', errors='replace'),
                    ArgReader(emu, next_x=3, next_d=0))
    data = text.encode('utf-8', errors='replace')
    if size:
        trunc = data[:max(0, size - 1)]
        emu.write_bytes(buf, trunc + b'\x00')
    emu.set_return(len(data))  # real snprintf returns the would-be full length
    emu.return_to_caller()


def h_vsnprintf(emu):
    buf, size = emu.get_arg(0), emu.get_arg(1)
    text = _format(emu, emu.read_cstr(emu.get_arg(2)).decode('utf-8', errors='replace'),
                    VaListReader(emu, emu.get_arg(3)))
    data = text.encode('utf-8', errors='replace')
    if size:
        trunc = data[:max(0, size - 1)]
        emu.write_bytes(buf, trunc + b'\x00')
    emu.set_return(len(data))
    emu.return_to_caller()


def h_sscanf(emu):
    text = emu.read_cstr(emu.get_arg(0)).decode('latin1', errors='replace')
    fmt = emu.read_cstr(emu.get_arg(1)).decode('latin1', errors='replace')
    convs = re.findall(r'%[^%diuxXofcsFeEgG]*[diuxXofcsFeEgG]', fmt)
    out_ptrs = [emu.get_arg(2 + k) for k in range(len(convs))]
    pos, count = 0, 0
    for k, conv in enumerate(convs):
        c = conv[-1]
        m = re.match(r'\s*', text[pos:])
        pos += m.end()
        if c in 'di':
            m = re.match(r'[+-]?\d+', text[pos:])
            if not m:
                break
            emu.write_u32(out_ptrs[k], int(m.group()) & 0xFFFFFFFF)
        elif c == 'u':
            m = re.match(r'\d+', text[pos:])
            if not m:
                break
            emu.write_u32(out_ptrs[k], int(m.group()))
        elif c in 'xX':
            m = re.match(r'[0-9a-fA-F]+', text[pos:])
            if not m:
                break
            emu.write_u32(out_ptrs[k], int(m.group(), 16))
        elif c == 's':
            m = re.match(r'\S+', text[pos:])
            if not m:
                break
            emu.write_bytes(out_ptrs[k], m.group().encode('latin1') + b'\x00')
        elif c in 'fFeEgG':
            m = re.match(r'[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?', text[pos:])
            if not m:
                break
            raw = struct.unpack('<I', struct.pack('<f', float(m.group())))[0]
            emu.write_u32(out_ptrs[k], raw)
        elif c == 'c':
            if pos >= len(text):
                break
            emu.write_u8(out_ptrs[k], ord(text[pos]))
            pos += 1
            count += 1
            continue
        else:
            break
        pos += m.end()
        count += 1
    emu.set_return(count)
    emu.return_to_caller()


def h_feof(emu):
    emu.set_return(0)
    emu.return_to_caller()


def h_fseek(emu):
    stream, offset, whence = emu.get_arg(0), emu.get_arg(1), emu.get_arg(2)
    if offset & 0x8000000000000000:
        offset -= 1 << 64
    f = emu.open_files.get(stream)
    if f is not None:
        try:
            f.seek(offset, {0: 0, 1: 1, 2: 2}.get(whence, 0))
        except OSError:
            pass
    emu.set_return(0)
    emu.return_to_caller()


def h_ftell(emu):
    f = emu.open_files.get(emu.get_arg(0))
    emu.set_return(f.tell() if f is not None else 0)
    emu.return_to_caller()


def h_fwrite(emu):
    ptr, size, nmemb, stream = emu.get_arg(0), emu.get_arg(1), emu.get_arg(2), emu.get_arg(3)
    data = emu.read_bytes(ptr, size * nmemb) if size and nmemb else b''
    f = emu.open_files.get(stream)
    if f is not None and data:
        f.write(data)
    emu.set_return(nmemb)
    emu.return_to_caller()


# ---- NVIDIA NvOs file/mutex API (best-effort reconstruction; likely
#      unused by a pure in-memory single-file compile like the example) ----
def h_NvOsFopen(emu):
    path_ptr, flags, handle_out = emu.get_arg(0), emu.get_arg(1), emu.get_arg(2)
    path = emu.read_cstr(path_ptr).decode('utf-8', errors='replace')
    try:
        f = open(path, 'r+b') if __import__('os').path.exists(path) else open(path, 'w+b')
    except OSError:
        emu.set_return(1)  # nonzero = NvError failure
        emu.return_to_caller()
        return
    handle = emu._next_file_handle
    emu._next_file_handle += 1
    emu.open_files[handle] = f
    if handle_out:
        emu.write_u64(handle_out, handle)
    emu.set_return(0)
    emu.return_to_caller()


def h_NvOsFclose(emu):
    f = emu.open_files.pop(emu.get_arg(0), None)
    if f is not None:
        f.close()
    emu.set_return(0)
    emu.return_to_caller()


def h_NvOsFread(emu):
    handle, ptr, size, bytes_out = emu.get_arg(0), emu.get_arg(1), emu.get_arg(2), emu.get_arg(3)
    f = emu.open_files.get(handle)
    data = f.read(size) if f is not None else b''
    if data:
        emu.write_bytes(ptr, data)
    if bytes_out:
        emu.write_u64(bytes_out, len(data))
    emu.set_return(0)
    emu.return_to_caller()


def h_NvOsFwrite(emu):
    handle, ptr, size = emu.get_arg(0), emu.get_arg(1), emu.get_arg(2)
    f = emu.open_files.get(handle)
    if f is not None and size:
        f.write(emu.read_bytes(ptr, size))
    emu.set_return(0)
    emu.return_to_caller()


def h_NvOsFseek(emu):
    handle, offset, whence = emu.get_arg(0), emu.get_arg(1), emu.get_arg(2)
    if offset & 0x8000000000000000:
        offset -= 1 << 64
    f = emu.open_files.get(handle)
    if f is not None:
        try:
            f.seek(offset, {0: 0, 1: 1, 2: 2}.get(whence, 0))
        except OSError:
            pass
    emu.set_return(0)
    emu.return_to_caller()


def h_NvOsFtell(emu):
    handle, pos_out = emu.get_arg(0), emu.get_arg(1)
    f = emu.open_files.get(handle)
    if f is not None and pos_out:
        emu.write_u64(pos_out, f.tell())
    emu.set_return(0)
    emu.return_to_caller()


def h_NvOsMutexCreate(emu):
    out = emu.get_arg(0)
    if out:
        emu.write_u64(out, emu.heap.alloc(8))  # any distinct nonzero handle
    emu.set_return(0)
    emu.return_to_caller()


def h_NvOsMutexLock(emu):
    emu.return_to_caller()


def h_NvOsMutexUnlock(emu):
    emu.return_to_caller()


def h_NvOsMutexDestroy(emu):
    emu.return_to_caller()


_HANDLERS = {
    'memcpy': h_memcpy, 'memmove': h_memmove, 'memset': h_memset, 'memcmp': h_memcmp,
    'strlen': h_strlen, 'strcpy': h_strcpy, 'strncpy': h_strncpy,
    'strcat': h_strcat, 'strncat': h_strncat,
    'strcmp': h_strcmp, 'strncmp': h_strncmp, 'strcasecmp': h_strcasecmp,
    'strchr': h_strchr, 'strrchr': h_strrchr, 'strstr': h_strstr, 'strtok': h_strtok,

    'tolower': h_tolower, 'toupper': h_toupper,
    'isalnum': h_isalnum, 'isalpha': h_isalpha, 'isspace': h_isspace,

    'atoi': h_atoi, 'atof': h_atof, 'strtol': h_strtol, 'qsort': h_qsort,

    'sqrt': h_sqrt, 'sin': h_sin, 'cos': h_cos, 'tan': h_tan,
    'asin': h_asin, 'acos': h_acos, 'atan': h_atan, 'atan2': h_atan2,
    'tanh': h_tanh, 'exp': h_exp, 'exp2': h_exp2, 'exp2f': h_exp2f,
    'log': h_log, 'logf': h_logf, 'pow': h_pow, 'fmod': h_fmod,
    'ldexp': h_ldexp, 'frexp': h_frexp,

    'finite': h_finite, 'expf': h_expf,

    '__errno_location': h_errno_location, 'getenv': h_getenv, 'exit': h_exit,
    'clock': h_clock,

    'setjmp': h_setjmp, 'longjmp': h_longjmp,

    '__cxa_guard_acquire': h_cxa_guard_acquire,
    '__cxa_guard_release': h_cxa_guard_release,
    '__cxa_guard_abort': h_cxa_guard_abort,
    '_ZN2nn2ro12ProtectRelroEPKvS2_S2_S2_S2_': h_nn_ro_ProtectRelro,

    'pthread_mutex_lock': h_pthread_mutex_lock, 'pthread_mutex_unlock': h_pthread_mutex_unlock,
    'pthread_cond_wait': h_pthread_cond_wait, 'pthread_cond_broadcast': h_pthread_cond_broadcast,

    'newlocale': h_newlocale, 'freelocale': h_freelocale, 'uselocale': h_uselocale,
    'mbtowc': h_mbtowc, '__ctype_get_mb_cur_max': h_ctype_get_mb_cur_max,

    '_Znwm': h_Znwm, '_ZdlPv': h_ZdlPv,

    'glslc_Alloc': h_glslc_Alloc, 'glslc_Realloc': h_glslc_Realloc,
    'glslc_Free': h_glslc_Free, 'glslc_GetAllocator': h_glslc_GetAllocator,

    '__nnmusl_init_dso': h_nnmusl_init_dso,

    'printf': h_printf, 'puts': h_puts, 'fputs': h_fputs,
    'fputc': h_fputc, 'putc': h_putc, 'fflush': h_fflush,
    'fprintf': h_fprintf, 'vfprintf': h_vfprintf,
    'sprintf': h_sprintf, 'vsprintf': h_vsprintf,
    'snprintf': h_snprintf, 'vsnprintf': h_vsnprintf,
    'sscanf': h_sscanf,
    'feof': h_feof, 'fseek': h_fseek, 'ftell': h_ftell, 'fwrite': h_fwrite,

    'NvOsFopen': h_NvOsFopen, 'NvOsFclose': h_NvOsFclose,
    'NvOsFread': h_NvOsFread, 'NvOsFwrite': h_NvOsFwrite,
    'NvOsFseek': h_NvOsFseek, 'NvOsFtell': h_NvOsFtell,
    'NvOsMutexCreate': h_NvOsMutexCreate, 'NvOsMutexLock': h_NvOsMutexLock,
    'NvOsMutexUnlock': h_NvOsMutexUnlock, 'NvOsMutexDestroy': h_NvOsMutexDestroy,
}
