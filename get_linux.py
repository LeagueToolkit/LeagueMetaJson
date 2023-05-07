#!python
import os
import tarfile
import urllib.request
from tarfile import TarFile, TarInfo

INIT = b'''#!/bin/busybox sh
echo "Installing busybox"
/usr/bin/busybox --install -s

echo "Mounting esential folders"
mount -t devtmpfs none /dev
mount -t proc none /proc
mount -t sysfs none /sys
mount -t tmpfs none /tmp

echo "Loading modules for 9p"
/usr/bin/depmod
/usr/bin/modprobe virtio_pci 9pnet_virtio
mkdir -p /share
mount -t 9p -o trans=virtio,version=9p2000.L host0 /share || echo "Failed to mount share"

echo "Running next stage"
export PATH="/lutris-ge-lol-7.0.8-x86_64/bin:$PATH"
/bin/sh +m -c /share/init.sh || echo "Failed to execute /share/init.sh"
#sh -i

echo "Bye bye"
poweroff -f
'''

PKGS = [
    # filesystem + kernel:
    ('filesystem.pkg.tar.zst', 'https://archlinux.org/packages/core/x86_64/filesystem/download/'),
    ('linux.pkg.tar.zst', 'https://archlinux.org/packages/core/x86_64/linux/download/'),
    # libc
    ('glibc.pkg.tar.zst', 'https://archlinux.org/packages/core/x86_64/glibc/download/'),
    ('gcc-libs.pkg.tar.zst', 'https://archlinux.org/packages/core/x86_64/gcc-libs/download/'),
    # dynamic kernel module support
    ('openssl.pkg.tar.zst', 'https://archlinux.org/packages/core/x86_64/openssl/download/'),
    ('xz.pkg.tar.zst', 'https://archlinux.org/packages/core/x86_64/xz/download/'),
    ('zlib.pkg.tar.zst', 'https://archlinux.org/packages/core/x86_64/zlib/download/'),
    ('zstd.pkg.tar.zst', 'https://archlinux.org/packages/core/x86_64/zstd/download/'),
    ('kmod.pkg.tar.zst', 'https://archlinux.org/packages/core/x86_64/kmod/download/'),
    # a shell
    ('busybox.pkg.tar.zst', 'https://archlinux.org/packages/community/x86_64/busybox/download/'),
    # wine
    ('wine-lutris-ge-lol.tar.xz', "https://github.com/GloriousEggroll/wine-ge-custom/releases/download/7.0-GE-8-LoL/wine-lutris-ge-lol-7.0.8-x86_64.tar.xz"),
]

class CPIO:
    class Entry:
        def __init__(self, name: str, mode: int = 0, mtime: int = 0, nlink: int = 1):
            self.name = name
            self.mode = mode
            self.uid = 0
            self.gid = 0
            self.devmajor = 0
            self.devminor = 0
            self.rdevmajor = 0
            self.rdevminor = 0
            self.mtime = mtime
            self.nlink = nlink
            self.data = b''
            self.children = {}

    def __init__(self):
        self.root = CPIO.Entry('.', mode = 0o040777, nlink = 2)

    def read(self, name):
        while name.endswith('/'): name = name[:-1]
        cur = self.root
        *dirname, basename = name.split('/')
        for part in dirname:
            if part == '.' or part == '':
                continue
            if part == '*':
                cur = next(iter(cur.children.values()))
            else:
                cur = cur.children[part]
            assert((cur.mode & 0o170000) == 0o040000)
        return cur.children[basename].data

    def add(self, name, data: bytes, mode: int, mtime: int = 0):
        while name.endswith('/'): name = name[:-1]
        cur = self.root
        *dirname, basename = name.split('/')
        for part in dirname:
            cur.mtime = max(cur.mtime, mtime)
            if part == '.' or part == '':
                continue
            if not part in cur.children:
                new = CPIO.Entry(name, 0o040777, mtime)
                cur.children[part] = new
            cur = cur.children[part]
            assert((cur.mode & 0o170000) == 0o040000)
        if not basename in cur.children:
            cur.children[basename] = CPIO.Entry(basename)
        result = cur.children[basename]
        result.mode = mode
        result.mtime = mtime
        result.data = data

    def add_folder(self, name: str, mode: int = 0o777, mtime: int = 0):
        return self.add(name, b'', 0o040000 | mode, mtime)

    def add_file(self, name: str, data: bytes = b'', mode: int = 0o777, mtime: int = 0):
        return self.add(name, data, 0o100000 | mode, mtime)

    def add_sym(self, name: str, data: bytes = b'', mode: int = 0o777, mtime: int = 0):
        return self.add(name, data, 0o120000 | mode, mtime)

    def add_from_tar(self, filename: str, filterf: lambda tarinfo: True = None):
        if filename.endswith('.zst') or filename.endswith('.zstd'):
            import zstandard
            with open(filename, 'rb') as fh:
                dctx = zstandard.ZstdDecompressor()
                with dctx.stream_reader(fh, read_size=512) as reader:
                    with tarfile.open(None, "r:", reader) as tar:
                        self.add_from_tarfile(tar, filterf)
                        return
        with tarfile.open(filename, "r") as tar:
            self.add_from_tarfile(tar, filterf)

    def add_from_tarfile(self, tar: TarFile, filterf: lambda tarinfo: True = None):
        for tarinfo in tar:
            if filterf and not filterf(tarinfo):
                continue
            if tarinfo.isdir():
                self.add_folder(tarinfo.name, tarinfo.mode, tarinfo.mtime)
            elif tarinfo.isreg():
                data = tar.extractfile(tarinfo).read()
                self.add_file(tarinfo.name, data, tarinfo.mode, tarinfo.mtime)
            elif tarinfo.issym():
                data = tarinfo.linkname.encode('utf-8')
                self.add_sym(tarinfo.name, data, tarinfo.mode, tarinfo.mtime)
            else:
                print("Unknown type ", tarinfo.name, tarinfo.type)

    def write_to_fileobj(self, out):
        q = [ (self.root, '.') ]
        ino = 0
        while q:
            ino += 1
            entry, path = q.pop()
            out.write(b'070701')
            out.write(b'%08X' % (ino,))
            out.write(b'%08X' % (entry.mode,))
            out.write(b'%08X' % (entry.uid,))
            out.write(b'%08X' % (entry.gid,))
            out.write(b'%08X' % (entry.nlink,))
            out.write(b'%08X' % (entry.mtime,))
            out.write(b'%08X' % (len(entry.data),))
            out.write(b'%08X' % (entry.devmajor,))
            out.write(b'%08X' % (entry.devminor,))
            out.write(b'%08X' % (entry.rdevmajor,))
            out.write(b'%08X' % (entry.rdevminor,))
            out.write(b'%08X' % (len(path) + 1,))     # plus null terminator
            out.write(b'%08X' % (0,))                 # crc of header, 0 to disable
            out.write(path.encode('utf-8'))
            out.write(b'\0' * (4 - (out.tell() % 4))) # align 4, adds implicit \0 when already aligned
            out.write(entry.data)
            if out.tell() % 4 != 0: out.write(b'\0' * (4 - (out.tell() % 4))) # align 4
            for c in reversed(entry.children.values()):
                q.append((c, f'{path}/{c.name}' if path not in ('', '.', '/') else c.name))
        out.write(b'07070100000000000000000000000000000000000000010000000000000000000000000000000000000000000000000000000B00000000TRAILER!!!')
        out.write(b'\0' * (512 - (out.tell() % 512)))

    def write_to_file(self, filename):
        with open(filename, 'wb') as out:
            self.write_to_fileobj(out)

def fetch_packages(pkgfolder):
    print("Fetching packages...")
    os.makedirs(pkgfolder, exist_ok=True)
    for basename, url in PKGS:
        name = f"{pkgfolder}/{basename}"
        print(url)
        if not os.path.exists(name):
            urllib.request.urlretrieve(url, f"{pkgfolder}/tmp")
            os.rename(f"{pkgfolder}/tmp", name)

def make_roofts(out, pkgfolder):
    os.makedirs(out, exist_ok=True)
    cpio = CPIO()
    print("Installing packages...")
    for basename, url in PKGS:
        cpio.add_from_tar(f"{pkgfolder}/{basename}")
    cpio.add_file('init', INIT)
    print("Creating ramdisk...")
    cpio.write_to_file(f'{out}/initrd')
    print("Extracting kernel...")
    with open(f'{out}/vmlinuz', 'wb') as outf:
        outf.write(cpio.read("usr/lib/modules/*/vmlinuz"))

fetch_packages('tmp/pkg')
make_roofts('tmp', 'tmp/pkg')
