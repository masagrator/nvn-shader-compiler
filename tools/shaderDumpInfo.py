# provide as argument input folder, it will generate "DUMP.yaml" in working directory.
# pass -o <output_folder> to instead dump one YAML file per input file into that folder
# (the folder is created if it doesn't exist).

import yaml
import glob
import sys
import os
import argparse

parser = argparse.ArgumentParser(description="Dump shader metadata from unpacked NVN shader files.")
parser.add_argument("input_folder", help="Folder containing the unpacked shader files")
parser.add_argument("-o", "--output-folder", dest="output_folder", default=None,
                     help="If given, dump one YAML file per input file into this folder (created if missing), instead of a single DUMP.yaml")
args = parser.parse_args()

files = glob.glob(f"{args.input_folder}/*.*")

if (args.output_folder is not None):
    os.makedirs(args.output_folder, exist_ok=True)

class GLSLC:
    SECTION_TYPE_GPU_CODE = 0
    SECTION_TYPE_ASM_DUMP = 1
    SECTION_TYPE_PERF_STATS = 2
    SECTION_TYPE_REFLECTION = 3
    SECTION_TYPE_DEBUG_INFO = 4

def presenter2(dumper, data):
    if '\n' in data:
        return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='|')
    return dumper.represent_scalar('tag:yaml.org,2002:str', data)

def ProcessDebugInfo(file, section_offset):
    RESULT = {}
    pos = file.tell()
    file.seek(section_offset)
    magic = int.from_bytes(file.read(4), "little")
    assert(magic == 0x65040891)
    RESULT["TYPE"] = "DEBUG_INFO"
    file.seek(section_offset + 0x30)
    text_length = int.from_bytes(file.read(4), "little")
    header_length = int.from_bytes(file.read(4), "little")
    file.seek(section_offset + header_length)
    text_bytes = file.read(text_length)
    text = text_bytes.decode("utf-8", errors="replace")
    # the embedded text uses \r\n (and sometimes lone \r) line endings, and some
    # parts even contain literal (already-escaped) "\r\n" as four separate text
    # characters. All of these need to become plain "\n" -- if we reintroduce
    # actual \r bytes (as a previous version of this code did), YAML literal
    # block style ('|') can't represent them and silently falls back to an
    # escaped quoted string.
    text = text.replace("\\r\\n", "\n")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # part of the embedded source is itself a C-string-escaped blob (also using
    # literal "\t" for indentation) rather than raw control characters. Expand
    # those to spaces rather than real tabs: YAML literal block style silently
    # falls back to a quoted/escaped string if the content contains an actual
    # tab character, same as it does for trailing whitespace or \r.
    text = text.replace("\\t", "    ")
    # the blob is a C-string; drop the trailing NUL terminator (and any other stray
    # NULs), since those also break YAML literal block style
    text = text.replace("\x00", "")
    # YAML literal block style also gets silently abandoned (falling back to a
    # quoted string) if any line has trailing whitespace, so strip it per-line.
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    RESULT["SOURCE"] = text
    file.seek(pos)
    return RESULT

def Process(magic, file):
    ENTRY = {}
    base = file.tell()
    if (magic == 0x12345679):
        ENTRY["TYPE"] = "CODE"
        ENTRY["SHADER_TYPE"] = "COMPUTE"
    elif (magic == 0x12345678):
        ENTRY["TYPE"] = "CODE"
        file.seek(base + 0x30)
        CommonWord0 = int.from_bytes(file.read(4), "little")
        ENTRY["SPH_TYPE"] = CommonWord0 & 0b11111
        match(ENTRY["SPH_TYPE"]):
            case 1: ENTRY["SPH_TYPE"] = "VertexTessGeometry"
            case 2: ENTRY["SPH_TYPE"] = "PixelShader"
        ENTRY["VERSION"] = (CommonWord0 & 0b1111100000) >> 5
        ENTRY["SHADER_TYPE"] = (CommonWord0 & 0b11110000000000) >> 10
        match(ENTRY["SHADER_TYPE"]):
            case 1: ENTRY["SHADER_TYPE"] = "VERTEX"
            case 2: ENTRY["SHADER_TYPE"] = "TESS_INIT"
            case 3: ENTRY["SHADER_TYPE"] = "TESS"
            case 4: ENTRY["SHADER_TYPE"] = "GEOMETRY"
            case 5: ENTRY["SHADER_TYPE"] = "PIXEL"
        ENTRY["MRT_ENABLE"] = bool((CommonWord0 & 0b100000000000000) >> 14)
        ENTRY["KILL_PIXELS"] = bool((CommonWord0 & 0b1000000000000000) >> 15)
        ENTRY["GLOBAL_STORE"] = bool((CommonWord0 & 0b10000000000000000) >> 16)
        ENTRY["SASS_VERSION"] = (CommonWord0 & 0b111100000000000000000) >> 17
        ENTRY["RESERVED0"] = (CommonWord0 & 0b1000000000000000000000) >> 21
        ENTRY["RESERVED1"] = (CommonWord0 & 0b10000000000000000000000) >> 22
        ENTRY["RESERVED2"] = (CommonWord0 & 0b100000000000000000000000) >> 23
        ENTRY["FAST_GS"] = bool((CommonWord0 & 0b1000000000000000000000000) >> 24)
        ENTRY["VSH_UNK_FLAG"] = bool((CommonWord0 & 0b10000000000000000000000000) >> 25)
        ENTRY["LOAD_OR_STORE"] = bool((CommonWord0 & 0b100000000000000000000000000) >> 26)
        ENTRY["FP64"] = bool((CommonWord0 & 0b1000000000000000000000000000) >> 27)
        ENTRY["STREAM_OUT_MASK"] = (CommonWord0 & 0b11110000000000000000000000000000) >> 28
        CommonWord1 = int.from_bytes(file.read(4), "little")
        CommonWord2 = int.from_bytes(file.read(4), "little")
        ENTRY["SH_LOCAL_MEM"] = (CommonWord1 & 0b111111111111111111111111) + ((CommonWord2 & 0b111111111111111111111111) << 24) # Dunno if good
        ENTRY["PER_PATCH_ATTR_CNT"] = (CommonWord1 & 0b11111111000000000000000000000000) >> 24
        ENTRY["THR_PER_INPUT_PRIM"] = (CommonWord2 & 0b11111111000000000000000000000000) >> 24
        CommonWord3 = int.from_bytes(file.read(4), "little")
        ENTRY["SH_LOCAL_MEM_CRS_SZ"] = (CommonWord3 & 0b111111111111111111111111)
        ENTRY["OUTPUT_TOPOLOGY"] = (CommonWord3 & 0b11110000000000000000000000000000) >> 24
        ENTRY["RESERVED3"] = (CommonWord3 & 0b111100000000000000000000000000000000) >> 28
        CommonWord4 = int.from_bytes(file.read(4), "little")
        ENTRY["MAX_OUT_VTX_CNT"] = (CommonWord4 & 0b111111111111)
        ENTRY["STORE_REQ_START"] = (CommonWord4 & 0b11111111000000000000) >> 12
        ENTRY["RESERVED4"] = (CommonWord4 & 0b111100000000000000000000) >> 20
        ENTRY["STORE_REQ_END"] = (CommonWord4 & 0b11111111000000000000000000000000) >> 24
    elif (magic == 0x98761234):
        #print("offset: 0x%x" % file.tell())
        ENTRY["TYPE"] = "CONTROL"
        file.seek(4, 1)
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
            file.seek(base + glasm_offset)
            #print("glasm: 0x%x" % file.tell())
            text = file.read(glasm_size).decode("ascii")
            file.seek(pos)
            ENTRY["GLASM"] = text
        else:
            ENTRY["GLASM"] = None
        file.seek(base + 0x20)
        unk_section_offset = int.from_bytes(file.read(4), "little")
        unk_section_size = int.from_bytes(file.read(4), "little") # always 0
        control_size = unk_section_offset + unk_section_size
        file.seek(base + 0x790)
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
        file.seek(base + 0x714)
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
        file.seek(base + 0x7D0)
        control_hash = file.read(8).hex().upper()
        glasm_hash = file.read(8).hex().upper()
        code_hash = file.read(8).hex().upper()
        ENTRY["CONTROL_HASH"] = control_hash
        ENTRY["GLASM_HASH"] = glasm_hash # it doesn't change when GLASM is identical but control and code are different, it's possible that also this is a hash of source file
        ENTRY["CODE_HASH"] = code_hash
    elif (magic == 0x19866891):
        ENTRY["TYPE"] = "OUTPUT"
        ENTRY["DATA"] = []
        file.seek(0x4C)
        section_num = int.from_bytes(file.read(4), "little")
        for i in range(section_num):
            file.seek(0x90 + (i * 0x90))
            size = int.from_bytes(file.read(4), "little")
            offset = int.from_bytes(file.read(4), "little")
            type = int.from_bytes(file.read(4), "little")
            if (type > GLSLC.SECTION_TYPE_DEBUG_INFO):
                continue
            if (type == GLSLC.SECTION_TYPE_DEBUG_INFO):
                ENTRY["DATA"].append(ProcessDebugInfo(file, offset))
                continue
            elif (type != GLSLC.SECTION_TYPE_GPU_CODE):
                ENTRY3 = {}
                match(type):
                    case GLSLC.SECTION_TYPE_ASM_DUMP: ENTRY3["TYPE"] = "ASM_DUMP"
                    case GLSLC.SECTION_TYPE_PERF_STATS: ENTRY3["TYPE"] = "PERF_STATS"
                    case GLSLC.SECTION_TYPE_REFLECTION: ENTRY3["TYPE"] = "REFLECTION"
                ENTRY["DATA"].append(ENTRY3)
                continue
            file.seek(0x28, 1)
            code_offset = int.from_bytes(file.read(4), "little")
            file.seek(offset)
            magic = int.from_bytes(file.read(4), "little")
            file.seek(-4, 1)
            ENTRY2 = []
            ENTRY2.append(Process(magic, file))
            file.seek(offset + code_offset)
            #print("0x%x" % file.tell())
            magic = int.from_bytes(file.read(4), "little")
            file.seek(-4, 1)
            ENTRY2.append(Process(magic, file))
            ENTRY["DATA"].append(ENTRY2)
    elif (magic == 0x19292919):
        ENTRY["TYPE"] = "REFLECTION"
    return ENTRY


yaml.add_representer(str, presenter2)
yaml.add_representer(str, presenter2, Dumper=yaml.SafeDumper)

# When dumping to a single DUMP.yaml, open it once up-front and stream each
# file's entry into it as soon as it's processed, instead of building up the
# whole DATA dict in memory and dumping it all at the end.
dump_file = None
if (args.output_folder is None):
    dump_file = open("DUMP.yaml", "w", encoding="UTF-8")

for i in range(len(files)):
    print(files[i])
    file = open(files[i], "rb")
    magic = int.from_bytes(file.read(4), "little")
    file.seek(0, 2)
    file_size = file.tell()
    file.seek(0)
    ENTRY = Process(magic, file)
    file.close()

    if (args.output_folder is not None):
        out_name = os.path.basename(files[i]) + ".yaml"
        out_path = os.path.join(args.output_folder, out_name)
        out_file = open(out_path, "w", encoding="UTF-8")
        yaml.safe_dump({files[i]: ENTRY}, out_file, sort_keys=False, default_flow_style=False)
        out_file.close()
    else:
        yaml.safe_dump({files[i]: ENTRY}, dump_file, sort_keys=False, default_flow_style=False)

    # drop the reference now that it's written out, so it can be garbage collected
    del ENTRY

if (args.output_folder is None):
    dump_file.close()
    print("Dumped metadata to DUMP.yaml")
else:
    print(f"Dumped {len(files)} YAML files to {args.output_folder}")
