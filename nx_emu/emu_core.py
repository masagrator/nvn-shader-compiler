"""
Core Unicorn AArch64 emulator wrapper.

Memory layout (all addresses are arbitrary choices, just spaced apart so
nothing collides -- there's no ASLR/randomization, this is a throwaway
single-shot process):

    0x00100000 .. 0x00110000   STUB_REGION   landing pads for ~90 imported
                                              libc/NvOs/glslc_Alloc functions
    0x10000000 .. +module size MODULE        glslc.elf's PT_LOAD segments
    0x40000000 .. +256MB       HEAP          bump allocator (malloc/new/
                                              glslc_Alloc all draw from here)
    0x50000000 .. +8MB         STACK         grows down from the top

Calling convention: standard AAPCS64. Integer/pointer args in X0-X7,
double args in D0-D7, integer return in X0, double return in D0.

The "call a guest function from Python" trick: set LR (X30) to a sentinel
address that is deliberately never mapped, then uc.emu_start(func, sentinel).
Unicorn stops as soon as PC == sentinel (it never has to fetch an
instruction there), which happens naturally when the callee executes RET.
The same trick is reused *inside* stub handlers to call back into guest
code (e.g. qsort's comparator callback), so nested calls just work.
"""
import struct
import sys

from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE, UC_HOOK_MEM_UNMAPPED, UcError
from unicorn.arm64_const import *

from . import elf_loader
from .heap import GuestHeap

PAGE = 0x1000

STUB_BASE = 0x00100000
STUB_REGION_SIZE = 0x00010000
STUB_SLOT_SIZE = 0x10          # 16 bytes/slot, way more than the 4 we use

MODULE_BASE = 0x10000000

HEAP_BASE = 0x40000000
HEAP_SIZE = 0x10000000         # 256MB

STACK_BASE = 0x50000000
STACK_SIZE = 0x00800000        # 8MB
STACK_TOP = STACK_BASE + STACK_SIZE

RETURN_SENTINEL = 0x0000DEAD0000   # never mapped -> emu_start(until=...) just stops here

_RET_INSN = struct.pack('<I', 0xD65F03C0)   # "ret"

_XREGS = [globals()[f'UC_ARM64_REG_X{i}'] for i in range(29)] + \
         [UC_ARM64_REG_X29, UC_ARM64_REG_X30]
_DREGS = [globals()[f'UC_ARM64_REG_D{i}'] for i in range(32)]


class EmulatorError(RuntimeError):
    pass


class GuestExit(Exception):
    """Raised by the exit() stub. Propagates up through emu_start()/
    call_guest_function() so the driver script can report it instead of
    the emulator just hanging or faulting."""
    def __init__(self, code):
        self.code = code
        super().__init__(f"guest called exit({code})")


class Emulator:
    def __init__(self, elf_path, debug=False):
        self.debug = debug
        self.uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)

        # ---- map fixed regions ----
        self.uc.mem_map(STUB_BASE, STUB_REGION_SIZE)               # RWX by default
        filler = _RET_INSN * (STUB_REGION_SIZE // 4)
        self.uc.mem_write(STUB_BASE, filler)

        self.uc.mem_map(HEAP_BASE, HEAP_SIZE)
        self.heap = GuestHeap(HEAP_BASE, HEAP_SIZE)

        self.uc.mem_map(STACK_BASE, STACK_SIZE)
        self.uc.reg_write(UC_ARM64_REG_SP, STACK_TOP - 0x100)

        # ---- stub bookkeeping ----
        self._stub_names_by_addr = {}
        self._stub_addr_by_name = {}
        self._stub_handlers = {}
        self._next_stub_slot = 0

        # data symbols resolved directly (not via a callable stub)
        self._data_syms = {}

        # per-process state used by libc_shim.py
        self.open_files = {}
        self._next_file_handle = 0x1000
        self.jmpbufs = {}
        self.errno_addr = self.heap.alloc(8)
        self.write_u32(self.errno_addr, 0)

        self.uc.hook_add(UC_HOOK_CODE, self._on_stub_code, begin=STUB_BASE,
                          end=STUB_BASE + STUB_REGION_SIZE - 1)
        self.uc.hook_add(UC_HOOK_MEM_UNMAPPED, self._on_unmapped)

        # ---- load & relocate the module ----
        self.info = elf_loader.load_elf(elf_path, MODULE_BASE)
        self._map_module()

        from . import libc_shim
        libc_shim.register_all(self)

        self._relocate()
        self.symbols = {
            s['name']: MODULE_BASE + s['value']
            for s in self.info.dynsyms
            if s['shndx'] != 'SHN_UNDEF' and s['name']
        }
        self._run_init()

    # ------------------------------------------------------------------
    # module mapping / relocation
    # ------------------------------------------------------------------
    def _map_module(self):
        for vaddr, memsz, filesz, data, flags in self.info.segments:
            start = elf_loader._page_align_down(vaddr)
            end = elf_loader._page_align_up(vaddr + memsz)
            self.uc.mem_map(MODULE_BASE + start, end - start)
            if data:
                self.uc.mem_write(MODULE_BASE + vaddr, data)
            # zero the bss tail (memsz > filesz)
            if memsz > filesz:
                self.uc.mem_write(MODULE_BASE + vaddr + filesz, b'\x00' * (memsz - filesz))
        # NOTE: everything is mapped RWX for simplicity. This is an emulator
        # for a single trusted local file, not a security sandbox.

    def _relocate(self):
        def mem_write64(off, value):
            self.uc.mem_write(MODULE_BASE + off, struct.pack('<Q', value))

        def resolve(name, _unused):
            return self._resolve_import(name)

        unresolved = elf_loader.apply_relocations(self.info, mem_write64, resolve)
        if unresolved and self.debug:
            print(f"[!] {len(unresolved)} relocations with unhandled types were skipped", file=sys.stderr)

    def _resolve_import(self, name):
        # A handful of "imports" are really linker-synthesized data symbols
        # marking the bounds of this same module's own relocation tables.
        # __nnmusl_init_dso would normally use these to register the module
        # with musl's runtime; since we do relocation ourselves up front we
        # never need init_dso to actually do anything, but we still give
        # these symbols real values in case something reads them.
        if name == '__rel_dyn_start' and self.info.rela_dyn_vaddr is not None:
            return MODULE_BASE + self.info.rela_dyn_vaddr
        if name == '__rel_dyn_end' and self.info.rela_dyn_vaddr is not None:
            return MODULE_BASE + self.info.rela_dyn_vaddr + self.info.rela_dyn_size
        if name == '__rel_plt_start' and self.info.rela_plt_vaddr is not None:
            return MODULE_BASE + self.info.rela_plt_vaddr
        if name == '__rel_plt_end' and self.info.rela_plt_vaddr is not None:
            return MODULE_BASE + self.info.rela_plt_vaddr + self.info.rela_plt_size
        if name == '__nnDetailNintendoSdkRuntimeObjectFile':
            # Unknown internal layout; hand back a zeroed scratch buffer so
            # a defensive read doesn't dereference NULL.
            if name not in self._data_syms:
                self._data_syms[name] = self.heap.alloc(256)
            return self._data_syms[name]

        return self.get_stub_addr(name)

    def get_stub_addr(self, name):
        if name in self._stub_addr_by_name:
            return self._stub_addr_by_name[name]
        idx = self._next_stub_slot
        self._next_stub_slot += 1
        addr = STUB_BASE + idx * STUB_SLOT_SIZE
        if addr >= STUB_BASE + STUB_REGION_SIZE:
            raise EmulatorError("stub region exhausted -- raise STUB_REGION_SIZE")
        self._stub_addr_by_name[name] = addr
        self._stub_names_by_addr[addr] = name
        return addr

    def register_stub(self, name, handler):
        """handler(emu) -> None. Must call emu.set_return()/emu.return_void()
        (or emu.tail_call_guest() for longjmp-style non-local jumps) before
        returning."""
        self.get_stub_addr(name)  # make sure a slot exists even if unused
        self._stub_handlers[name] = handler

    def _run_init(self):
        if self.info.init_addr:
            self.call_guest_function(MODULE_BASE + self.info.init_addr, [0, 0, 0])
        if self.info.init_array_vaddr and self.info.init_array_size:
            count = self.info.init_array_size // 8
            raw = self.uc.mem_read(MODULE_BASE + self.info.init_array_vaddr, count * 8)
            for i in range(count):
                fn = struct.unpack_from('<Q', raw, i * 8)[0]
                if fn:
                    self.call_guest_function(fn, [0, 0, 0])

    # ------------------------------------------------------------------
    # stub dispatch
    # ------------------------------------------------------------------
    def _on_stub_code(self, uc, address, size, user_data):
        name = self._stub_names_by_addr.get(address)
        if name is None:
            if self.debug:
                print(f"[!] hit stub region at {address:#x} with no registered symbol", file=sys.stderr)
            self.return_to_caller()
            return
        handler = self._stub_handlers.get(name)
        if handler is None:
            if self.debug:
                print(f"[!] call to unimplemented import '{name}' -- returning 0", file=sys.stderr)
            self.set_return(0)
            self.return_to_caller()
            return
        if self.debug:
            print(f"[stub] {name}(x0={self.get_arg(0):#x}, x1={self.get_arg(1):#x}, "
                  f"x2={self.get_arg(2):#x}, x3={self.get_arg(3):#x})", file=sys.stderr)
        handler(self)

    def _on_unmapped(self, uc, access, address, size, value, user_data):
        pc = self.uc.reg_read(UC_ARM64_REG_PC)
        sym = self._nearest_symbol(pc)
        print(f"[FATAL] unmapped access type={access} addr={address:#x} size={size} "
              f"at PC={pc:#x} ({sym})", file=sys.stderr)
        return False  # let unicorn raise UcError

    def _nearest_symbol(self, pc):
        best_name, best_addr = None, -1
        for s in self.info.dynsyms:
            if s['shndx'] == 'SHN_UNDEF' or not s['name']:
                continue
            addr = MODULE_BASE + s['value']
            if addr <= pc and addr > best_addr:
                best_addr, best_name = addr, s['name']
        if best_name:
            return f"module+{pc - MODULE_BASE:#x} (near {best_name}+{pc - best_addr:#x})"
        return f"module+{pc - MODULE_BASE:#x}"

    # ------------------------------------------------------------------
    # calling convention helpers
    # ------------------------------------------------------------------
    def call_guest_function(self, addr, args=(), fp_args=()):
        for i, a in enumerate(args):
            self.uc.reg_write(_XREGS[i], a & 0xFFFFFFFFFFFFFFFF)
        for i, d in enumerate(fp_args):
            self.uc.reg_write(_DREGS[i], struct.unpack('<Q', struct.pack('<d', d))[0])
        self.uc.reg_write(UC_ARM64_REG_X30, RETURN_SENTINEL)
        self.uc.reg_write(UC_ARM64_REG_PC, addr)
        try:
            self.uc.emu_start(addr, RETURN_SENTINEL)
        except UcError as e:
            pc = self.uc.reg_read(UC_ARM64_REG_PC)
            raise EmulatorError(f"emulation fault calling {addr:#x}: {e} (stopped at PC={pc:#x}, "
                                 f"{self._nearest_symbol(pc)})") from e
        return self.uc.reg_read(UC_ARM64_REG_X0)

    def get_arg(self, i):
        return self.uc.reg_read(_XREGS[i])

    def get_farg(self, i):
        raw = self.uc.reg_read(_DREGS[i]) & 0xFFFFFFFFFFFFFFFF
        return struct.unpack('<d', struct.pack('<Q', raw))[0]

    def get_farg32(self, i):
        raw = self.uc.reg_read(_DREGS[i]) & 0xFFFFFFFF
        return struct.unpack('<f', struct.pack('<I', raw))[0]

    def set_return(self, value):
        self.uc.reg_write(UC_ARM64_REG_X0, value & 0xFFFFFFFFFFFFFFFF)

    def set_freturn(self, value):
        self.uc.reg_write(UC_ARM64_REG_D0, struct.unpack('<Q', struct.pack('<d', value))[0])

    def set_freturn32(self, value):
        raw = struct.unpack('<I', struct.pack('<f', value))[0]
        self.uc.reg_write(UC_ARM64_REG_D0, raw)

    def get_lr(self):
        """Capture LR *before* making any nested guest call from within a
        stub handler -- a nested call_guest_function() will overwrite X30,
        so handlers that call back into guest code (qsort's comparator)
        must save this first and use return_to(saved_lr) at the end instead
        of return_to_caller()."""
        return self.uc.reg_read(UC_ARM64_REG_X30)

    def return_to(self, addr):
        self.uc.reg_write(UC_ARM64_REG_PC, addr)

    def return_to_caller(self):
        lr = self.uc.reg_read(UC_ARM64_REG_X30)
        self.uc.reg_write(UC_ARM64_REG_PC, lr)

    def return_void(self):
        self.return_to_caller()

    # ------------------------------------------------------------------
    # guest memory helpers
    # ------------------------------------------------------------------
    def read_bytes(self, addr, n):
        return bytes(self.uc.mem_read(addr, n))

    def write_bytes(self, addr, data):
        self.uc.mem_write(addr, data)

    def read_cstr(self, addr, max_len=1 << 20):
        if addr == 0:
            return b''
        out = bytearray()
        chunk = 64
        while len(out) < max_len:
            data = self.read_bytes(addr + len(out), chunk)
            nul = data.find(b'\x00')
            if nul != -1:
                out += data[:nul]
                return bytes(out)
            out += data
        return bytes(out)

    def write_cstr(self, s):
        if isinstance(s, str):
            s = s.encode('utf-8')
        addr = self.heap.alloc(len(s) + 1)
        self.write_bytes(addr, s + b'\x00')
        return addr

    def write_blob(self, data):
        """Allocate len(data) bytes and write them as-is, with no NUL
        terminator appended. Use this (not write_cstr) for binary payloads
        such as raw SPIR-V module words, which may contain embedded zero
        bytes and whose length is tracked separately (e.g. GLSLCinput's
        spirvModuleSizes) rather than by scanning for a NUL."""
        if isinstance(data, str):
            data = data.encode('utf-8')
        addr = self.heap.alloc(max(1, len(data)))
        self.write_bytes(addr, bytes(data))
        return addr

    def read_u8(self, addr):
        return self.read_bytes(addr, 1)[0]

    def read_u32(self, addr):
        return struct.unpack('<I', self.read_bytes(addr, 4))[0]

    def read_i32(self, addr):
        return struct.unpack('<i', self.read_bytes(addr, 4))[0]

    def read_u64(self, addr):
        return struct.unpack('<Q', self.read_bytes(addr, 8))[0]

    def write_u8(self, addr, v):
        self.write_bytes(addr, bytes([v & 0xFF]))

    def write_u32(self, addr, v):
        self.write_bytes(addr, struct.pack('<I', v & 0xFFFFFFFF))

    def write_u64(self, addr, v):
        self.write_bytes(addr, struct.pack('<Q', v & 0xFFFFFFFFFFFFFFFF))
