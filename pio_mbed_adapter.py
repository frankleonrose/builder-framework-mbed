# Copyright 2019-present PlatformIO <contact@platformio.org>
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

import sys
import json
from os.path import (abspath, basename, isfile, join, relpath,
                     normpath, sep)

from tools.build_api import (prepare_toolchain, merge_region_list,
                             UPDATE_WHITELIST)
from tools.targets import TARGET_MAP
from tools.utils import generate_update_filename
from tools.resources import FileType, Resources
from pio_mock_notifier import PlatformioFakeNotifier

# A handy global as PlatformIO supports only GCC toolchain
TOOLCHAIN_NAME = "GCC_ARM"
# Possible profiles: debug, develop, release
BUILD_PROFILE = "release"


def get_notifier():
    # Not used by PlatformIO, but requried by mbed build api internals.
    return PlatformioFakeNotifier()


def fix_path(path):
    # mbed build api provides the relative path (FileRef) with two
    # redundant directories at the beginning
    path_dirs = path[0].split(sep)[2:]
    if not path_dirs:
        return ""
    return join(*path_dirs)


def fix_paths(paths):
    result = []
    for path in paths:
        path = fix_path(path)
        if not path:
            continue
        result.append(path)

    return result


class PlatformioMbedAdapter(object):
    def __init__(self,
                 src_paths,
                 build_path,
                 target,
                 framework_path,
                 app_config=None,
                 ignore_dirs=None,
                 toolchain_name=TOOLCHAIN_NAME):
        self.src_paths = src_paths
        self.build_path = build_path
        self.target = target
        self.framework_path = framework_path
        self.app_config = app_config
        self.ignore_dirs = ignore_dirs
        self.toolchain_name = toolchain_name
        self.build_profile = "release"
        self.toolchain = None
        self.resources = None
        self.notify = get_notifier()

    def get_build_profile(self):
        file_with_profiles = join(self.framework_path, "tools", "profiles",
                                  "%s.json" % self.build_profile)
        if not isfile(file_with_profiles):
            sys.stderr.write("Could not find the file with build profiles!\n")
            sys.exit(1)
        profiles = []
        contents = json.load(open(file_with_profiles))
        profiles.append(contents)

        return profiles

    def get_target_config(self):
        target_info = TARGET_MAP.get(self.target, "")
        if not target_info:
            sys.stderr.write(
                "Failed to extract info for %s target\n", self.target)
            sys.exit(1)

        return target_info

    def generate_mbed_config_file(self):
        self.toolchain.get_config_header()
        # assert isfile?

    def process_symbols(self, symbols):
        result = []
        for s in symbols:
            if "MBED_BUILD_TIMESTAMP" in s:
                # Skip to avoid recompiling the entire project
                continue
            elif '"' in s and ".h" in s:
                # for cases with includes in value like:
                # CMSIS_VECTAB_VIRTUAL_HEADER_FILE="cmsis_nvic.h"
                s = s.replace('"', '\\"')

            result.append(s)

        # Symbols need to be sorted to aboid recompilation
        result.sort()
        return result

    def needs_merging(self):
        return self.toolchain.config.has_regions

    def merge_apps(self, userprog_path, firmware_path):
        if self.toolchain.config.has_regions:
            region_list = list(self.toolchain.config.regions)
            region_list = [
                r._replace(filename=userprog_path) if r.active else r for r in region_list
            ]

            merge_region_list(region_list, firmware_path, self.notify)
            update_regions = [
                r for r in region_list if r.name in UPDATE_WHITELIST]

            if update_regions:
                update_res = join(
                    self.build_path,
                    generate_update_filename(
                        firmware_path, self.toolchain.target))
                merge_region_list(update_regions, update_res, self.notify)
                firmware_path = (firmware_path, update_res)
            else:
                firmware_path = (firmware_path, None)

    def extract_project_info(self, generate_config=False):
        """Extract comprehensive information in order to build a PlatformIO project

        src_paths - a list of paths that contain needed files to build project
        build_path - a path where mbed_config.h will be created
        target - suitable mbed target name
        framework_path = path to the root folder of the mbed framework package
        app_config - path to mbed_app.json
        ignore_dirs - doesn't work with GCC at the moment?
        """
        # Default values for mbed build api functions
        target = self.get_target_config()
        build_profile = self.get_build_profile()
        # ToDo: delete ? notify = get_notifier()

        jobs = 1  # how many compilers we can run at once
        name = None  # the name of the project
        dependencies_paths = None  # libraries location to include when linking
        macros = None  # additional macros
        inc_dirs = None  # additional dirs where include files may be found
        ignore = self.ignore_dirs  # list of paths to add to mbedignore
        clean = False  # Rebuild everything if True

        # Convert src_path to a list if needed
        if not isinstance(self.src_paths, list):
            self.src_paths = [self.src_paths]
        self.src_paths = [relpath(s) for s in self.src_paths]

        # Pass all params to the unified prepare_toolchain()
        self.toolchain = prepare_toolchain(
            self.src_paths, self.build_path, target, self.toolchain_name,
            macros=macros, clean=clean, jobs=jobs, notify=self.notify,
            app_config=self.app_config, build_profile=build_profile,
            ignore=ignore)

        # The first path will give the name to the library
        if name is None:
            name = basename(normpath(abspath(self.src_paths[0])))

        # Disabled for legacy libraries
        # for src_path in self.src_paths:
        #     if not exists(src_path):
        #         error_msg = "The library src folder doesn't exist:%s", src_path
        #         raise Exception(error_msg)

        self.resources = Resources(self.notify).scan_with_toolchain(
            self.src_paths, self.toolchain, dependencies_paths,
            inc_dirs=inc_dirs)

        src_files = (
            self.resources.get_file_refs(FileType.ASM_SRC) +
            self.resources.get_file_refs(FileType.C_SRC) +
            self.resources.get_file_refs(FileType.CPP_SRC)
        )

        if generate_config:
            self.generate_mbed_config_file()

        result = {
            "src_files": fix_paths(src_files),
            "inc_dirs": fix_paths(
                self.resources.get_file_refs(FileType.INC_DIR)),
            "ldscript": fix_paths(
                self.resources.get_file_refs(FileType.LD_SCRIPT)),
            "build_flags": self.toolchain.flags,
            "libs": [basename(l) for l in fix_paths(
                self.resources.get_file_refs(FileType.LIB))],
            "lib_paths": fix_paths(
                self.resources.get_file_refs(FileType.LIB_DIR)),
            "syslibs": self.toolchain.sys_libs,
            "build_symbols": self.process_symbols(
                self.toolchain.get_symbols()),
            "hex": fix_paths(self.resources.get_file_refs(FileType.HEX))
        }

        return result
