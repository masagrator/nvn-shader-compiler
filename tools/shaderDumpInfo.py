# provide as argument output folder, it will generate "DUMP.yaml" in working directory

import yaml
import glob
import sys

files = glob.glob(f"{sys.argv[1]}/*.*")

DATA = {}

def double_quote_presenter(dumper, data):
    """Force PyYAML to use double quotes ('"') for multiline strings."""
    if '\n' in data:
        # The magic happens here with style='"'
        return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='|')
    return dumper.represent_scalar('tag:yaml.org,2002:str', data)

for i in range(len(files)):
    file = open(files[i], "rb")
    file.seek(0, 2)
    file_size = file.tell()
    file.seek(0)
    magic = int.from_bytes(file.read(4), "little")
    ENTRY = {}
    if (magic == 0x12345679):
        ENTRY["TYPE"] = "CODE"
        ENTRY["SHADER_TYPE"] = "COMPUTE"
    elif (magic == 0x12345678):
        ENTRY["TYPE"] = "CODE"
        file.seek(0x30)
        CommonWord0 = int.from_bytes(file.read(4), "little")
        ENTRY["SPH_TYPE"] = CommonWord0 & 0b11111
        match(ENTRY["SPH_TYPE"]):
            case 1: ENTRY["SPH_TYPE"] = "VertexTessGeometry"
            case 2: ENTRY["SPH_TYPE"] = "PixelShader"
        ENTRY["VERSION"] = CommonWord0 & 0b1111100000 >> 5
        ENTRY["SHADER_TYPE"] = CommonWord0 & 0b11110000000000 >> 10
        match(ENTRY["SHADER_TYPE"]):
            case 1: ENTRY["SHADER_TYPE"] = "VERTEX"
            case 2: ENTRY["SHADER_TYPE"] = "TESS_INIT"
            case 3: ENTRY["SHADER_TYPE"] = "TESS"
            case 4: ENTRY["SHADER_TYPE"] = "GEOMETRY"
            case 5: ENTRY["SHADER_TYPE"] = "PIXEL"
        ENTRY["MRT_ENABLE"] = bool(CommonWord0 & 0b100000000000000 >> 14)
        ENTRY["KILL_PIXELS"] = bool(CommonWord0 & 0b1000000000000000 >> 15)
        ENTRY["GLOBAL_STORE"] = bool(CommonWord0 & 0b10000000000000000 >> 16)
        ENTRY["SASS_VERSION"] = CommonWord0 & 0b111100000000000000000 >> 17
        ENTRY["RESERVED0"] = CommonWord0 & 0b1000000000000000000000 >> 21
        ENTRY["RESERVED1"] = CommonWord0 & 0b10000000000000000000000 >> 22
        ENTRY["RESERVED2"] = CommonWord0 & 0b100000000000000000000000 >> 23
        ENTRY["FAST_GS"] = bool(CommonWord0 & 0b1000000000000000000000000 >> 24)
        ENTRY["VSH_UNK_FLAG"] = bool(CommonWord0 & 0b10000000000000000000000000 >> 25)
        ENTRY["LOAD_OR_STORE"] = bool(CommonWord0 & 0b100000000000000000000000000 >> 26)
        ENTRY["FP64"] = bool(CommonWord0 & 0b1000000000000000000000000000 >> 27)
        ENTRY["STREAM_OUT_MASK"] = CommonWord0 & 0b11110000000000000000000000000000 >> 28
        CommonWord1 = int.from_bytes(file.read(4), "little")
        CommonWord2 = int.from_bytes(file.read(4), "little")
        ENTRY["SH_LOCAL_MEM"] = CommonWord1 & 0b111111111111111111111111 + ((CommonWord2 & 0b111111111111111111111111) << 24) # Dunno if good
        ENTRY["PER_PATCH_ATTR_CNT"] = CommonWord1 & 0b11111111000000000000000000000000 >> 24
        ENTRY["THR_PER_INPUT_PRIM"] = CommonWord2 & 0b11111111000000000000000000000000 >> 24
        CommonWord3 = int.from_bytes(file.read(4), "little")
        ENTRY["SH_LOCAL_MEM_CRS_SZ"] = CommonWord3 & 0b111111111111111111111111
        ENTRY["OUTPUT_TOPOLOGY"] = CommonWord3 & 0b11110000000000000000000000000000 >> 24
        ENTRY["RESERVED3"] = CommonWord3 & 0b111100000000000000000000000000000000 >> 28
        CommonWord4 = int.from_bytes(file.read(4), "little")
        ENTRY["MAX_OUT_VTX_CNT"] = CommonWord4 & 0b111111111111
        ENTRY["STORE_REQ_START"] = CommonWord4 & 0b11111111000000000000 >> 12
        ENTRY["RESERVED4"] = CommonWord4 & 0b111100000000000000000000 >> 20
        ENTRY["STORE_REQ_END"] = CommonWord4 & 0b11111111000000000000000000000000 >> 24
    elif (magic == 0x98761234):
        ENTRY["TYPE"] = "CONTROL"
        gpu_major = int.from_bytes(file.read(4), "little")
        gpu_minor = int.from_bytes(file.read(4), "little")
        ENTRY["NVN_VERSION"] = "%d.%d" % (gpu_major, gpu_minor)
        arch = int.from_bytes(file.read(4), "little")
        impl = int.from_bytes(file.read(4), "little")
        if (arch == 0x120 and impl == 0xB):
            ENTRY["ARCH"] = "GM20B"
        else:
            ENTRY["ARCH"] = "0x%x/0x%x" % (arch, impl)
        glasm_offset = int.from_bytes(file.read(4), "little")
        glasm_size = int.from_bytes(file.read(4), "little")
        if (glasm_size > 0):
            pos = file.tell()
            file.seek(glasm_offset)
            text = file.read(glasm_size).decode("ascii")
            file.seek(pos)
            ENTRY["GLASM"] = text
        else:
            ENTRY["GLASM"] = None
        file.seek(0x20)
        unk_section_offset = int.from_bytes(file.read(4), "little")
        unk_section_size = int.from_bytes(file.read(4), "little") # always 0
        control_size = unk_section_offset + unk_section_size
        assert(control_size == file_size)
        file.seek(0x790)
        flags = int.from_bytes(file.read(8), "little")
        ENTRY["FLAGS"] = []
        if (flags & 1 == 1):
            ENTRY["FLAGS"].append("GLSL-SEPARABLE")
        if (flags & 2 == 2):
            ENTRY["FLAGS"].append("OUTPUT-ASSEMBLY")
        if (flags & 4 == 4):
            ENTRY["FLAGS"].append("OUTPUT-GPU-BINARIES")
        if (flags & 8 == 8):
            ENTRY["FLAGS"].append("OUTPUT-PERM-STATS")
        if (flags & 0x10 == 0x10):
            ENTRY["FLAGS"].append("OUTPUT-REFLECTION")
        if (flags & 0x20 == 0x20):
            ENTRY["INPUT_LANGUAGE"] = "GLES"
        elif (flags & 0x40 == 0x40):
            ENTRY["INPUT_LANGUAGE"] = "SPIR-V"
        else:
            ENTRY["INPUT_LANGUAGE"] = "GLSL"
        if (flags & 0x600 != 0):
            level = ((flags & 0x600) >> 9) - 1
            ENTRY["FLAGS"].append("DEBUG-LEVEL_G%d" % level)
        if (flags & 0x2000 == 0x2000):
            ENTRY["FLAGS"].append("SPILL-CONTROL_NO-SPILL")
        if (flags & 0x20000 == 0x20000):
            ENTRY["FLAGS"].append("OUTPUT-THIN-GPU-BINARIES")
        if (flags & 0x40000 == 0x40000):
            ENTRY["FLAGS"].append("TESSELATION-AND-PASSTHROUGH-GS")
        if (flags & 0x80000 == 0x80000):
            ENTRY["FLAGS"].append("PRIORITIZE-CONSECUTIVE-TEXTURE-INSTRUCTIONS")
        if (flags & 0x1F00000 != 0):
            mask = (flags & 0x1F00000) >> 20
            ENTRY["FLAGS"].append("FAST-MATH-MASK_0x%X" % mask)
        if (flags & 0x4000000 == 0x4000000):
            ENTRY["FLAGS"].append("OPT-LEVEL_NONE")
        if (flags & 0x20000000 == 0x20000000):
            ENTRY["FLAGS"].append("UNROLL-CONTROL_NONE")
        if (flags & 0x40000000 == 0x40000000):
            ENTRY["FLAGS"].append("UNROLL-CONTROL_ALL")
        if (flags & 0x100000000 == 0x100000000):
            ENTRY["FLAGS"].append("ERROR-ON-SCRATCH-MEM-USE")
        if (flags & 0x200000000 == 0x200000000):
            ENTRY["FLAGS"].append("CBF-OPTIMIZATION")
        if (flags & 0x400000000 == 0x400000000):
            ENTRY["FLAGS"].append("WARP-CULLING")
        if (flags & 0x800000000 == 0x800000000):
            ENTRY["FLAGS"].append("MULTITHREADED-COMPILATION")
        if (flags & 0x1000000000 == 0x1000000000):
            ENTRY["FLAGS"].append("WARN-UNINIT_NONE")
        if (flags & 0x2000000000 == 0x2000000000):
            ENTRY["FLAGS"].append("WARN-UNINIT_ALL")
        file.seek(0x714)
        type = file.read(1)[0]
        match(type):
            case 0:
                ENTRY["STAGE"] = "VERTEX"
            case 1:
                ENTRY["STAGE"] = "FRAGMENT"
            case 2:
                ENTRY["STAGE"] = "GEOMETRY"
            case 3:
                ENTRY["STAGE"] = "TESS_CONTROL"
            case 4:
                ENTRY["STAGE"] = "TESS_EVALUATION"
            case 5:
                ENTRY["STAGE"] = "COMPUTE"
            case _:
                print("Unknown stage: %d!" % type)
                sys.exit()
        file.seek(0x7D0)
        code_hash = file.read(8).hex().upper()
        control_hash = file.read(8).hex().upper()
        unk_hash = file.read(8).hex().upper()
        ENTRY["CODE_HASH"] = code_hash
        ENTRY["CONTROL_HASH"] = control_hash
        ENTRY["UNK_HASH"] = unk_hash
    file.close()
    DATA[files[i]] = ENTRY

yaml.add_representer(str, double_quote_presenter)
yaml.add_representer(str, double_quote_presenter, Dumper=yaml.SafeDumper)
file = open("DUMP.yaml", "w", encoding="UTF-8")
yaml.safe_dump(DATA, file, sort_keys=False, default_flow_style=False)
file.close()
