# Copyright 2014-present PlatformIO <contact@platformio.org>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
mbed

The mbed framework The mbed SDK has been designed to provide enough
hardware abstraction to be intuitive and concise, yet powerful enough to
build complex projects. It is built on the low-level ARM CMSIS APIs,
allowing you to code down to the metal if needed. In addition to RTOS,
USB and Networking libraries, a cookbook of hundreds of reusable
peripheral and module libraries have been built on top of the SDK by
the mbed Developer Community.

http://mbed.org/
"""

import sys
from os.path import basename, isdir, isfile, join

from SCons.Script import DefaultEnvironment

from platformio import util
from platformio.builder.tools.piolib import PlatformIOLibBuilder

env = DefaultEnvironment()

FRAMEWORK_DIR = env.PioPlatform().get_package_dir("framework-mbed")
assert isdir(FRAMEWORK_DIR)


class CustomLibBuilder(PlatformIOLibBuilder):

    PARSE_SRC_BY_H_NAME = False

    # Max depth of nested includes:
    # -1 = unlimited
    # 0 - disabled nesting
    # >0 - number of allowed nested includes
    CCONDITIONAL_SCANNER_DEPTH = 0

    # For cases when sources located not only in "src" dir
    @property
    def src_dir(self):
        return self.path


def get_mbed_config(target):
    config_file = join(FRAMEWORK_DIR, "platformio", "variants", target,
                       target + ".json")
    if not isfile(config_file):
        sys.stderr.write("Cannot find the configuration file for your board! "
                         "Please read instructions here %s\n" % join(
                             FRAMEWORK_DIR, "platformio", "README.txt"))
        env.Exit(1)

    return util.load_json(config_file)


def get_dynamic_manifest(name, config, extra_inc_dirs=[]):
    manifest = {
        "name": "mbed-" + name,
        "build": {
            "flags": ["-I.."],
            "srcFilter": ["-<*>"],
            "libArchive": False
        }
    }

    manifest['build']['flags'].extend(
        ['-I "%s"' % d for d in config.get("inc_dirs")])

    for d in extra_inc_dirs:
        manifest['build']['flags'].extend(['-I "%s"' % d.replace("\\", "/")])

    src_files = config.get("c_sources") + \
        config.get("s_sources") + config.get("cpp_sources")
    for f in src_files:
        manifest['build']['srcFilter'].extend([" +<%s>" % f])

    manifest['build']['libLDFMode'] = "deep+"

    # Implicit dependencies:
    # OnboardNetworkStack::get_default_instance
    # mbed::mbed_event_queue
    if name == "netsocket":
        manifest['build']['flags'].extend(["-DMBED_CONF_EVENTS_PRESENT"])
        manifest['dependencies'] = {
            "mbed-lwipstack": "*",
            "mbed-events": "*"
        }

    # arm_random_module_init
    if name == "mbed-client-randlib":
        manifest['dependencies'] = {"mbed-nanostack-interface": "*"}

    if name == "nfc":
        manifest['dependencies'] = {"mbed-events": "*"}

    return manifest


def process_global_lib(libname, lib_configs):
    if not libname or not lib_configs:
        return

    lib_config = lib_configs.get(libname, {})
    if not lib_config:
        return
    lib_includes = [
        join(FRAMEWORK_DIR, lib_config.get("dir"), f)
        for f in lib_config.get("inc_dirs")
    ]

    env.Append(
        CPPPATH=lib_includes,
        LIB_DEPS=["mbed-%s" % libname]
    )


variants_remap = util.load_json(
    join(FRAMEWORK_DIR, "platformio", "variants_remap.json"))
board_type = env.subst("$BOARD")
variant = variants_remap[
    board_type] if board_type in variants_remap else board_type.upper()
variant = env.BoardConfig().get("build.variant", variant)

mbed_config = get_mbed_config(variant)

env.Replace(
    AS="$CC",
    ASCOM="$ASPPCOM"
)

env.Append(
    ASFLAGS=mbed_config.get("build_flags").get("asm") +
    mbed_config.get("build_flags").get("common"),
    CCFLAGS=mbed_config.get("build_flags").get("common"),
    CFLAGS=mbed_config.get("build_flags").get("c"),
    CXXFLAGS=mbed_config.get("build_flags").get("cxx"),
    LINKPPFLAGS=mbed_config.get("build_flags").get("ld"),
    LINKFLAGS=mbed_config.get("build_flags").get("ld"),
    LIBS=mbed_config.get("syslibs")
)

symbols = []
for s in mbed_config.get("symbols"):
    s = s.replace("\"", "\\\"")
    macro = s.split("=", 1)
    if len(macro) == 2 and macro[1].isdigit():
        symbols.append((macro[0], int(macro[1])))
    else:
        symbols.append(s)

env.Append(
    CPPDEFINES=symbols,
    LIBS=["c", "stdc++"]  # temporary fix for the linker issue
)


#
# Process libraries
#

# There is no difference in processing between lib and feature
libs = mbed_config.get("libs").copy()
libs.update(mbed_config.get("components"))
libs.update(mbed_config.get("features"))
libs.update(mbed_config.get("frameworks"))

#
# Process Core files from framework
#

env.Append(
    CPPPATH=[
        join(FRAMEWORK_DIR, d) for d in mbed_config.get("core").get("inc_dirs")
    ],

    LIBS=[
        env.File(join(FRAMEWORK_DIR, d)) for d in mbed_config.get("core").get("libraries")
    ]
)

env.Append(CPPPATH=[
    FRAMEWORK_DIR,
    join(FRAMEWORK_DIR, "platformio", "variants", variant)
])

MBED_RTOS = "PIO_FRAMEWORK_MBED_RTOS_PRESENT" in env.Flatten(
    env.get("CPPDEFINES", []))

if MBED_RTOS:
    if not libs.get("rtos"):
        print "Warning! This board doesn't support Mbed OS!"
    env.Append(CPPDEFINES=["MBED_CONF_RTOS_PRESENT"])
    process_global_lib("rtos", libs)

MBED_EVENTS = "PIO_FRAMEWORK_MBED_EVENTS_PRESENT" in env.Flatten(
    env.get("CPPDEFINES", []))

if MBED_EVENTS:
    env.Append(CPPDEFINES=["MBED_CONF_EVENTS_PRESENT"])
    process_global_lib("events", libs)

if "FEATURE_CRYPTOCELL310" in env.Flatten(env.get("CPPDEFINES", [])):
    process_global_lib("FEATURE_CRYPTOCELL310", libs)

core_src_files = mbed_config.get("core").get("s_sources") + mbed_config.get(
    "core").get("c_sources") + mbed_config.get("core").get("cpp_sources")

env.BuildSources(
    join("$BUILD_DIR", "FrameworkMbedCore"),
    FRAMEWORK_DIR,
    src_filter=["-<*>"] + [" +<%s>" % f for f in core_src_files])

if "nordicnrf5" in env.get("PIOPLATFORM"):
    softdevice_hex_path = join(FRAMEWORK_DIR,
                               mbed_config.get("softdevice_hex", ""))
    if softdevice_hex_path and isfile(softdevice_hex_path):
        env.Append(SOFTDEVICEHEX=softdevice_hex_path)
    else:
        print("Warning! Cannot find softdevice binary"
              "Firmware will be linked without it!")

#
# Generate linker script
#

env.Replace(LDSCRIPT_PATH=join(FRAMEWORK_DIR, mbed_config.get("ldscript")))
if not env.get("LDSCRIPT_PATH"):
    sys.stderr.write("Cannot find linker script for your board!\n")
    env.Exit(1)

linker_script = env.Command(
    join("$BUILD_DIR",
         "%s.link_script.ld" % basename(env.get("LDSCRIPT_PATH"))),
    "$LDSCRIPT_PATH",
    env.VerboseAction(
        '%s -E -P $LINKPPFLAGS $SOURCE -o $TARGET' %
        env.subst("$GDB").replace("-gdb", "-cpp"),
        "Generating LD script $TARGET"))

env.Depends("$BUILD_DIR/$PROGNAME$PROGSUFFIX", linker_script)
env.Replace(LDSCRIPT_PATH=linker_script)

#
# Initialize libraries for LDF
#

for lib, lib_config in libs.items():
    if not lib_config:
        continue
    extra_includes = []
    env.Append(EXTRA_LIB_BUILDERS=[
        CustomLibBuilder(env, join(FRAMEWORK_DIR, lib_config.get("dir")),
                         get_dynamic_manifest(lib, lib_config, extra_includes))
    ])
