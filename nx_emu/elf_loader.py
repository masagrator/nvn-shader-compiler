"""
Minimal AArch64 ELF "shared object" loader.

glslc.elf is an ET_DYN (position independent) ELF with NO DT_NEEDED entries.
Nothing dlopen()s it "for real" -- on author's own build machines a small
in-house host tool maps it, applies the RELA relocations itself, and supplies
about ninety libc/NvOs/glslc_Alloc symbols by hand. That's exactly what this
module (plus emu_core.py / libc_shim.py) reproduces.

Relocation types actually present in this binary (checked with readelf -r):
    R_AARCH64_RELATIVE   (1027)  -- base + addend, by far the most common
    R_AARCH64_JUMP_SLOT  (1026)  -- PLT GOT slot for an imported function
    R_AARCH64_GLOB_DAT   (1025)  -- GOT slot for an imported data symbol
There is no PT_TLS segment, so no thread-local-storage setup is required.
"""
import io
import struct
from elftools.elf.elffile import ELFFile
from elftools.elf.enums import ENUM_RELOC_TYPE_AARCH64

R_AARCH64_RELATIVE = ENUM_RELOC_TYPE_AARCH64['R_AARCH64_RELATIVE']
R_AARCH64_GLOB_DAT = ENUM_RELOC_TYPE_AARCH64['R_AARCH64_GLOB_DAT']
R_AARCH64_JUMP_SLOT = ENUM_RELOC_TYPE_AARCH64['R_AARCH64_JUMP_SLOT']
R_AARCH64_ABS64 = ENUM_RELOC_TYPE_AARCH64['R_AARCH64_ABS64']

PAGE = 0x1000


def _page_align_down(x):
    return x & ~(PAGE - 1)


def _page_align_up(x):
    return (x + PAGE - 1) & ~(PAGE - 1)


class LoadedELF:
    """Holds everything emu_core.py needs after mapping+relocating the file."""

    def __init__(self):
        self.base = 0
        self.entry_free = True   # ET_DYN, no real entry point
        self.segments = []       # list of (vaddr, memsz, filesz, data, perms)
        self.dynsyms = []        # list of dicts: name, value, shndx (defined?), size
        self.init_addr = None
        self.init_array_vaddr = None
        self.init_array_size = 0
        self.rela_dyn_vaddr = None
        self.rela_dyn_size = 0
        self.rela_plt_vaddr = None
        self.rela_plt_size = 0
        self.max_vaddr_end = 0


def load_elf(path, base):
    """Parse the ELF and return a LoadedELF with segment bytes ready to be
    mapped at `base`, but does NOT touch Unicorn -- keeps this module
    testable/usable without unicorn installed."""
    info = LoadedELF()
    info.base = base

    with open(path, 'rb') as f:
        data = f.read()

    ef = ELFFile(io.BytesIO(data))

    if ef['e_machine'] != 'EM_AARCH64':
        raise ValueError(f"expected AArch64 ELF, got {ef['e_machine']}")
    if ef['e_type'] != 'ET_DYN':
        raise ValueError(f"expected ET_DYN (shared object), got {ef['e_type']}")

    # ---- PT_LOAD segments ----
    for seg in ef.iter_segments():
        if seg['p_type'] != 'PT_LOAD':
            continue
        vaddr = seg['p_vaddr']
        memsz = seg['p_memsz']
        filesz = seg['p_filesz']
        off = seg['p_offset']
        flags = seg['p_flags']  # bit0=X bit1=W bit2=R (PF_X=1,PF_W=2,PF_R=4)
        seg_data = data[off:off + filesz]
        info.segments.append((vaddr, memsz, filesz, seg_data, flags))
        info.max_vaddr_end = max(info.max_vaddr_end, vaddr + memsz)

    # ---- dynsym ----
    dynsym = ef.get_section_by_name('.dynsym')
    if dynsym is None:
        raise ValueError("no .dynsym section -- unexpected for this file")
    for sym in dynsym.iter_symbols():
        info.dynsyms.append({
            'name': sym.name,
            'value': sym['st_value'],
            'shndx': sym['st_shndx'],  # 'SHN_UNDEF' if undefined/imported
            'size': sym['st_size'],
            'info': sym['st_info'],
        })

    # ---- .dynamic tags (INIT / INIT_ARRAY) ----
    dyn = ef.get_section_by_name('.dynamic')
    if dyn is not None:
        for tag in dyn.iter_tags():
            if tag.entry.d_tag == 'DT_INIT':
                info.init_addr = tag.entry.d_val
            elif tag.entry.d_tag == 'DT_INIT_ARRAY':
                info.init_array_vaddr = tag.entry.d_val
            elif tag.entry.d_tag == 'DT_INIT_ARRAYSZ':
                info.init_array_size = tag.entry.d_val

    # ---- relocation section bounds (used to resolve __rel_dyn_start etc) ----
    reld = ef.get_section_by_name('.rela.dyn')
    if reld is not None:
        info.rela_dyn_vaddr = reld['sh_addr']
        info.rela_dyn_size = reld['sh_size']
    relp = ef.get_section_by_name('.rela.plt')
    if relp is not None:
        info.rela_plt_vaddr = relp['sh_addr']
        info.rela_plt_size = relp['sh_size']

    # ---- collect raw relocations (both .rela.dyn and .rela.plt) ----
    info.relocations = []
    for secname in ('.rela.dyn', '.rela.plt'):
        sec = ef.get_section_by_name(secname)
        if sec is None:
            continue
        for reloc in sec.iter_relocations():
            info.relocations.append({
                'offset': reloc['r_offset'],
                'sym': reloc['r_info_sym'],
                'type': reloc['r_info_type'],
                'addend': reloc['r_addend'],
            })

    return info


def apply_relocations(info, mem_write64, resolve_symbol):
    """
    mem_write64(vaddr, value)  -- writes an 8-byte little-endian value at
                                   (info.base + vaddr) into guest memory.
    resolve_symbol(name, defined_value_or_None) -> int
                                   -- returns the final absolute address to
                                   place in the GOT slot for an imported
                                   (or exported-but-referenced) symbol.
    """
    unresolved = []
    for r in info.relocations:
        rtype = r['type']
        off = r['offset']

        if rtype == R_AARCH64_RELATIVE:
            mem_write64(off, info.base + r['addend'])
            continue

        if rtype in (R_AARCH64_GLOB_DAT, R_AARCH64_JUMP_SLOT, R_AARCH64_ABS64):
            sym = info.dynsyms[r['sym']]
            if sym['shndx'] != 'SHN_UNDEF':
                # Defined within this module itself.
                value = info.base + sym['value'] + (r['addend'] if rtype == R_AARCH64_ABS64 else 0)
            else:
                value = resolve_symbol(sym['name'], None)
            mem_write64(off, value)
            continue

        unresolved.append(r)

    return unresolved
