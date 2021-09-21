#!/usr/bin/env python
import os
import shutil
import lzma
import subprocess
import sys
import json
import glob
import urllib.request
import time

INIT_SCRIPT = """#!/bin/sh
export WINEDEBUG=-all
export RUST_BACKTRACE=1
export WINEDLLOVERRIDES="powrprof=n"
cd /share/lol
rm -rf meta/
mkdir meta/
timeout 300 wine League\ of\ Legends.exe foo.rofl
echo $? > exitcode
exit 0
"""

JSON_URL = "https://sieve.services.riotcdn.net/api/v1/products/lol/version-sets/{region}?q[platform]=windows&q[artifact_type_id]=lol-game-client&q[published]=true"

USER_AGENT = 'Mozilla/5.0 (Android 11; Mobile; rv:68.0) Gecko/68.0 Firefox/88.0'

DELAY = 5

# Ensure destination filename has folder path
def ensure_folder(dst_filepath):
    dst_dir = os.path.dirname(dst_filepath)
    os.makedirs(dst_dir, exist_ok=True)

# Runs a process
def run(name, *args):
    print(f"Running {name}")
    process = subprocess.call([ name, *args], bufsize=1, stdout=sys.stdout, stderr=sys.stderr)
    return not process

# Copy file
def copy_file(src_filepath, dst_filepath):
    print(f"Copying file {src_filepath} to {dst_filepath}")
    ensure_folder(dst_filepath)
    shutil.copyfile(src_filepath, dst_filepath)

# Prune folder
def prune_folder(dirname):
    if os.path.exists(dirname):
        shutil.rmtree(dirname)

# Extract lzma compressed file
def decompress_lzma(src_filepath, dst_filepath):
    print(f"Decompressing {src_filepath} to {dst_filepath}")
    ensure_folder(dst_filepath)
    with lzma.open(src_filepath, "rb") as src_file:
        with open(dst_filepath, "wb") as dst_file:
            while data := src_file.read(64 * 1024):
                dst_file.write(data)

# Generate executable script
def generate_script_file(dst_filepath, contents):
    print(f"Generating {dst_filepath}")
    ensure_folder(dst_filepath)
    with open(dst_filepath, "w") as dst_file:
        dst_file.write(INIT_SCRIPT)
    os.chmod(dst_filepath, 0o755)

def read_txt_file_or_empty(src_filepath):
    if not os.path.exists(src_filepath):
        return ""
    with open(src_filepath, 'r') as inf:
        return inf.read().rstrip()

def write_txt_file(dst_filepath, contents):
    ensure_folder(dst_filepath)
    with open(dst_filepath, "w") as dst_file:
        dst_file.write(contents)

# Download league files
def download_files(downloader, manifest, dst_dir, namefilter):
    print(f"Downloading {manifest} to {dst_dir}")
    os.makedirs(dst_dir, exist_ok=True)
    extension = '.exe' if os.name == 'nt' else ''
    assert(run(f'{downloader}{extension}', manifest, '-o', dst_dir, '-f', namefilter))

# Runs qemu
def run_qemu(bindir, workdir):
    print("Starting qemu")
    accel = []
    if os.path.exists("/dev/kvm"):
        print("Using KVM (might need sudo or user group)!")
        accel.append("-enable-kvm")
    else:
        print("No acceleration aveilable this might take a while!")

    assert(run("qemu-system-i386", *[
        *accel,
        "-cpu", "qemu64,-hypervisor",
        "-m", "1024",
        "-kernel", f"{bindir}/vmlinux",
        "-initrd", f"{bindir}/initrd",
        "-append", "console=hvc0 quiet",
        "-nodefaults",
        "-no-user-config",
        "-nographic",
        "-chardev", "stdio,id=virtiocon0",
        "-device", "virtio-serial-pci",
        "-device", "virtconsole,chardev=virtiocon0",
        "-drive", f"file={workdir}/wine.img,format=raw,index=0,media=disk",
        "-virtfs", f"local,path={workdir}/share,mount_tag=host0,security_model=mapped-xattr,id=host0",
    ]))

# Dump meta
def dump_meta(bindir, manifest, workdir, dst_dir):
    download_files(f'{bindir}/ManifestDownloader', manifest, f'{workdir}/share/lol', '\.dll|\.exe|Bootstrap\.windows')
    copy_file(f'{bindir}/powrprof.dll', f'{workdir}/share/lol/AkRecorder.dll')
    if not os.path.exists(f"{workdir}/wine.img"):
        decompress_lzma(f"{bindir}/wine.img.lzma", f"{workdir}/wine.img")
    generate_script_file(f"{workdir}/share/init.sh", INIT_SCRIPT)
    prune_folder("f'{workdir}/share/lol/meta")
    run_qemu(bindir, workdir)
    exitcode = int(open(f"{workdir}/share/lol/exitcode").read())
    assert(exitcode == 0)

def fetch_latest_version(region):
    url = JSON_URL.format(region = region)
    print(f"Fetching releases from: {url}")
    request = urllib.request.Request(url, headers={'User-Agent':USER_AGENT})
    response = urllib.request.urlopen(request, timeout=15)
    data = response.read().decode('utf-8')
    newversions = []
    for release in json.loads(data)["releases"]:
        version, name = release["compat_version"]["id"].split('+')
        manifest = release["download"]["url"]
        print(f'Version manifest: {version} manifest')
        newversions.append((version, manifest))
    assert(len(newversions) == 1)
    return newversions[0]

# Dump new version
def dump_meta_latest(bindir, region, workdir, dst_dir):
    version, manifest = fetch_latest_version(region)
    last_dumped_version = read_txt_file_or_empty(f'{dst_dir}/version.txt')
    print(f'Last dumped version is: {last_dumped_version}')
    if version == last_dumped_version:
        print(f'Up to date!')
        return
    dump_meta('bin', manifest, 'tmp', 'meta')
    meta_json, = list(glob.iglob(f'{workdir}/share/lol/meta/meta_*.json'))
    copy_file(meta_json, f'{dst_dir}/meta.json')
    write_txt_file(f'{dst_dir}/version.txt', version)

dump_meta_latest('bin', "EUW1", 'tmp', 'meta')
