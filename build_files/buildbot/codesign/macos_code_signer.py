# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
#
# ##### END GPL LICENSE BLOCK #####

# <pep8 compliant>

import logging
import subprocess

from pathlib import Path
from typing import List

import codesign.util as util

from buildbot_utils import Builder

from codesign.absolute_and_relative_filename import AbsoluteAndRelativeFileName
from codesign.base_code_signer import BaseCodeSigner

logger = logging.getLogger(__name__)
logger_server = logger.getChild('server')

# NOTE: Check is done as filename.endswith(), so keep the dot
EXTENSIONS_TO_BE_SIGNED = {'.dylib', '.so', '.dmg'}

# Prefixes of a file (not directory) name which are to be signed.
# Used to sign extra executable files in Contents/Resources.
NAME_PREFIXES_TO_BE_SIGNED = {'python'}


def is_file_from_bundle(file: AbsoluteAndRelativeFileName) -> bool:
    """
    Check whether file is coming from an .app bundle
    """
    parts = file.relative_filepath.parts
    if not parts:
        return False
    if not parts[0].endswith('.app'):
        return False
    return True


def get_bundle_from_file(
        file: AbsoluteAndRelativeFileName) -> AbsoluteAndRelativeFileName:
    """
    Get AbsoluteAndRelativeFileName descriptor of bundle
    """
    assert(is_file_from_bundle(file))

    parts = file.relative_filepath.parts
    bundle_name = parts[0]

    base_dir = file.base_dir
    bundle_filepath = file.base_dir / bundle_name
    return AbsoluteAndRelativeFileName(base_dir, bundle_filepath)


def is_bundle_executable_file(file: AbsoluteAndRelativeFileName) -> bool:
    """
    Check whether given file is an executable within an app bundle
    """
    if not is_file_from_bundle(file):
        return False

    parts = file.relative_filepath.parts
    num_parts = len(parts)
    if num_parts < 3:
        return False

    if parts[1:3] != ('Contents', 'MacOS'):
        return False

    return True


class MacOSCodeSigner(BaseCodeSigner):
    def check_file_is_to_be_signed(
            self, file: AbsoluteAndRelativeFileName) -> bool:
        if is_bundle_executable_file(file):
            return True

        base_name = file.relative_filepath.name
        if any(base_name.startswith(prefix)
               for prefix in NAME_PREFIXES_TO_BE_SIGNED):
            return True

        return file.relative_filepath.suffix in EXTENSIONS_TO_BE_SIGNED

    def codesign_remove_signature(
            self, file: AbsoluteAndRelativeFileName) -> None:
        """
        Make sure given file does not have codesign signature

        This is needed because codesigning is not possible for file which has
        signature already.
        """

        logger_server.info(
            'Removing codesign signature from  %s...', file.relative_filepath)

        command = ['codesign', '--remove-signature', file.absolute_filepath]
        self.run_command_or_mock(command, util.Platform.MACOS)

    def codesign_file(
            self, file: AbsoluteAndRelativeFileName) -> None:
        """
        Sign given file

        NOTE: File must not have any signatures.
        """

        logger_server.info(
            'Codesigning  %s...', file.relative_filepath)

        entitlements_file = self.config.MACOS_ENTITLEMENTS_FILE
        command = ['codesign',
                   '--timestamp',
                   '--options', 'runtime',
                   f'--entitlements="{entitlements_file}"',
                   '--sign', self.config.MACOS_CODESIGN_IDENTITY,
                   file.absolute_filepath]
        self.run_command_or_mock(command, util.Platform.MACOS)

    def codesign_all_files(self, files: List[AbsoluteAndRelativeFileName]) -> bool:
        """
        Run codesign tool on all eligible files in the given list.

        Will ignore all files which are not to be signed. For the rest will
        remove possible existing signature and add a new signature.
        """

        num_files = len(files)
        have_ignored_files = False
        signed_files = []
        for file_index, file in enumerate(files):
            # Ignore file if it is not to be signed.
            # Allows to manually construct ZIP of a bundle and get it signed.
            if not self.check_file_is_to_be_signed(file):
                logger_server.info(
                    'Ignoring file [%d/%d] %s',
                    file_index + 1, num_files, file.relative_filepath)
                have_ignored_files = True
                continue

            logger_server.info(
                'Running codesigning routines for file [%d/%d] %s...',
                file_index + 1, num_files, file.relative_filepath)

            self.codesign_remove_signature(file)
            self.codesign_file(file)

            signed_files.append(file)

        if have_ignored_files:
            logger_server.info('Signed %d files:', len(signed_files))
            num_signed_files = len(signed_files)
            for file_index, signed_file in enumerate(signed_files):
                logger_server.info(
                    '- [%d/%d] %s',
                    file_index + 1, num_signed_files,
                    signed_file.relative_filepath)

        return True

    def codesign_bundles(
            self, files: List[AbsoluteAndRelativeFileName]) -> None:
        """
        Codesign all .app bundles in the given list of files.

        Bundle is deducted from paths of the files, and every bundle is only
        signed once.
        """

        signed_bundles = set()

        for file in files:
            if not is_file_from_bundle(file):
                continue
            bundle = get_bundle_from_file(file)
            bundle_name = bundle.relative_filepath
            if bundle_name in signed_bundles:
                continue

            logger_server.info('Running codesign routines on bundle %s',
                               bundle_name)

            self.codesign_remove_signature(bundle)
            self.codesign_file(bundle)

            signed_bundles.add(bundle_name)

        return True

    def sign_all_files(self, files: List[AbsoluteAndRelativeFileName]) -> None:
        # TODO(sergey): Handle errors somehow.

        if not self.codesign_all_files(files):
            return

        if not self.codesign_bundles(files):
            return
