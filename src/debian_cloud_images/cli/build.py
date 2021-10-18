import argparse
import collections.abc
import enum
import json
import logging
import pathlib
import re

from datetime import datetime

from .base import BaseCommand

from ..build.fai import RunFAI
from ..build.manifest import CreateManifest
from ..build.tar import RunTar
from ..data import data_path
from ..utils import argparse_ext


logger = logging.getLogger()


class BuildType:
    def __init__(self, kw):
        def init(*, fai_classes, output_name, output_version, output_version_azure):
            self.fai_classes = fai_classes
            self.output_name = output_name
            self.output_version = output_version
            self.output_version_azure = output_version_azure
        init(**kw)


BuildTypeEnum = enum.Enum(  # type:ignore
                            # mypy is not able to parse functional Enum properly
    'BuildTypeEnum',
    {
        'dev': {
            'fai_classes': ('TYPE_DEV', ),
            'output_name': 'debian-{release}-{vendor}-{arch}-{build_type}-{build_id}-{version}',
            'output_version': '{version}',
            'output_version_azure': '0.0.{version!s}',
        },
        'official': {
            'fai_classes': (),
            'output_name': 'debian-{release}-{vendor}-{arch}-{build_type}-{version}',
            'output_version': '{date}-{version}',
            'output_version_azure': '0.{date!s}.{version!s}',
        },
    },
    type=BuildType,
)


class BuildId:
    re = re.compile(r"^[a-z][a-z0-9-]+$")

    def __init__(self, s):
        r = self.re.match(s)

        if not r:
            raise ValueError('invalid build id value')

        self.id = r.group(0)


class Classes(collections.abc.MutableSet):
    def __init__(self):
        self.__data = []

    def __contains__(self, v):
        return v in self.__data

    def __iter__(self):
        return iter(self.__data)

    def __len__(self):
        return len(self.__data)

    def add(self, v):
        logger.info('Adding class %s', v)
        self.__data.append(v)

    def discard(self, v):
        logger.info('Removing class %s', v)
        self.__data.remove(v)


class Check:
    def __init__(self):
        self.classes = Classes()
        self.classes.add('DEBIAN')
        self.classes.add('CLOUD')
        self.env = {}
        self.info = {}

    def set_type(self, _type):
        self.type = _type
        self.info['type'] = self.type.name
        self.classes |= self.type.fai_classes

    def set_release(self, release):
        self.release = release
        self.info['release'] = self.release.basename
        self.info['release_id'] = self.release.id
        self.info['release_baseid'] = self.release.baseid
        self.classes |= self.release.fai_classes

    def set_vendor(self, vendor):
        self.vendor = vendor
        self.env['CLOUD_RELEASE_ID'] = self.info['vendor'] = self.vendor.name
        self.classes |= self.vendor.fai_classes

    def set_arch(self, arch):
        self.arch = arch
        self.info['arch'] = arch.name
        self.classes |= arch.fai_classes

    def set_version(self, version, version_date, build_id):
        self.build_id = self.info['build_id'] = build_id.id

        self.version = self.type.output_version.format(
            version=version,
            date=version_date.strftime('%Y%m%d'),
        )
        self.version_azure = self.type.output_version_azure.format(
            version=version,
            date=version_date.strftime('%Y%m%d'),
        )

        self.env['CLOUD_RELEASE_VERSION'] = self.info['version'] = self.version
        if self.vendor.name == 'azure':
            self.env['CLOUD_RELEASE_VERSION_AZURE'] = self.info['version_azure'] = self.version_azure

    def check(self):
        if self.arch.name in self.release.arch_supports_linux_image_cloud and self.vendor.use_linux_image_cloud:
            self.classes.add('LINUX_IMAGE_CLOUD')
        else:
            self.classes.add('LINUX_IMAGE_BASE')
        self.classes.add('LAST')


class BuildCommand(BaseCommand):
    argparser_name = 'build'
    argparser_help = 'build Debian images'
    argparser_usage = '%(prog)s'

    @classmethod
    def _argparse_register(cls, parser):
        super()._argparse_register(parser)

        cls.argparser_argument_release = parser.add_argument(
            'release_name',
            help='Debian release to build',
            metavar='RELEASE',
        )
        cls.argparser_argument_vendor = parser.add_argument(
            'vendor_name',
            help='Vendor to build image for',
            metavar='VENDOR',
        )
        cls.argparser_argument_arch = parser.add_argument(
            'arch_name',
            help='Architecture or sub-architecture to build image for',
            metavar='ARCH',
        )
        parser.add_argument(
            '--build-id',
            metavar='ID',
            required=True,
            type=BuildId,
        )
        parser.add_argument(
            '--build-type',
            action=argparse_ext.ActionEnum,
            enum=BuildTypeEnum,
            default='dev',
            help='Type of image to build',
            metavar='TYPE',
        )
        parser.add_argument(
            '--noop',
            action='store_true',
            help='print the commands which would be executed, but do not run them'
        )
        parser.add_argument(
            '--localdebs',
            action='store_true',
            help='Read extra debs from localdebs directory',
        )
        parser.add_argument(
            '--output',
            default='.',
            help='write manifests and images to (default: .)',
            metavar='DIR',
            type=pathlib.Path
        )
        parser.add_argument(
            '--override-name',
            help='override name of output',
        )
        parser.add_argument(
            '--version',
            action=argparse_ext.ActionEnv,
            env='CI_PIPELINE_IID',
            help='version of image',
            metavar='VERSION',
            type=int,
        )
        parser.add_argument(
            '--version-date',
            default=datetime.now(),
            help='date part of version (default: today)',
            type=cls._argparse_type_date,
        )

    @staticmethod
    def _argparse_type_date(s):
        try:
            return datetime.strptime(s, "%Y-%m-%d")
        except ValueError:
            msg = "Given date ({0}) is not valid. Expected format: 'YYYY-MM-DD'".format(s)
            raise argparse.ArgumentTypeError(msg)

    def __init__(self, *, release_name=None, vendor_name=None, arch_name=None, version=None, build_id=None, build_type=None, localdebs=False, output=None, noop=False, override_name=None, version_date=None, **kw):
        super().__init__(**kw)

        arch = self.config_image.archs.get(arch_name)
        release = self.config_image.releases.get(release_name)
        vendor = self.config_image.vendors.get(vendor_name)

        if arch is None:
            raise argparse.ArgumentError(
                self.argparser_argument_arch,
                f'invalid value: {arch_name}, select one of {", ".join(self.config_image.archs)}')

        if vendor is None:
            raise argparse.ArgumentError(
                self.argparser_argument_vendor,
                f'invalid value: {vendor_name}, select one of {", ".join(self.config_image.vendors)}')

        if release is None:
            raise argparse.ArgumentError(
                self.argparser_argument_release,
                f'invalid value: {release_name}, select one of {", ".join(self.config_image.releases)}')

        self.noop = noop

        self.c = Check()
        self.c.set_type(build_type)
        self.c.set_release(release)
        self.c.set_vendor(vendor)
        self.c.set_arch(arch)
        self.c.set_version(version, version_date, build_id)
        if localdebs:
            self.c.classes.add('LOCALDEBS')
        self.c.check()

        name = override_name or self.c.type.output_name.format(
            build_type=self.c.type.name,
            release=self.c.release.name,
            vendor=self.c.vendor.name,
            arch=self.c.arch.name,
            version=self.c.version,
            build_id=self.c.build_id,
        )

        self.env = self.c.env
        self.env['CLOUD_BUILD_INFO'] = json.dumps(self.c.info)
        self.env['CLOUD_BUILD_NAME'] = name
        self.env['CLOUD_BUILD_OUTPUT_DIR'] = output.resolve()
        self.env['CLOUD_BUILD_DATA'] = data_path

        output.mkdir(parents=True, exist_ok=True)

        image_raw = output / '{}.raw'.format(name)
        image_tar = output / '{}.tar'.format(name)
        manifest_fai = output / '{}.build-fai.json'.format(name)
        manifest_final = output / '{}.build.json'.format(name)

        self.fai = RunFAI(
            output_filename=image_raw,
            classes=self.c.classes,
            size_gb=self.c.vendor.size,
            env=self.env,
        )

        self.tar = RunTar(
            input_filename=image_raw,
            output_filename=image_tar,
        )

        self.manifest = CreateManifest(
            input_filename=manifest_fai,
            output_filename=manifest_final,
            info=self.c.info,
        )

    def __call__(self):
        self.fai(not self.noop)
        digest = self.tar(not self.noop)
        self.manifest(not self.noop, (digest,))


if __name__ == '__main__':
    BuildCommand._main()
