#!/usr/bin/env python3
"""Fetch what a Linux cross build needs on a Windows (or any) host.

Writes into out/linux-cross/:
  sysroot/  Debian amd64 headers and libraries (glibc, libstdc++, SDL2, Vulkan, FFmpeg)
  tools/    glslangValidator and pkgconf for the host
  deck-lib/ FFmpeg 7.1 shared libraries to ship next to the binary, since SteamOS lacks them

Only the standard library is used. Symlinks in the packages are stored as copies,
so the sysroot works on NTFS without symlink privileges.
"""
import argparse
import hashlib
import io
import lzma
import os
import platform
import posixpath
import shutil
import sys
import tarfile
import urllib.request
import zipfile

MIRROR = "https://deb.debian.org/debian"
SUITE = "trixie"
GLSLANG_VERSION = "16.6.0"
PKGCONF_WHEEL = "pkgconf-3.0.7.post0-py3-none-win_amd64.whl"
# Self-contained FFmpeg build (glibc only, 2.28 floor) with the same 7.1 ABI as the sysroot.
FFMPEG_RELEASE = "autobuild-2026-07-31-14-10"
FFMPEG_ASSET = "ffmpeg-n7.1.5-12-g1fdbca85aa-linux64-lgpl-shared-7.1.tar.xz"
FFMPEG_SHA256 = "f5f0ad52c6ee28a222eb10838c231469a10ad325f84063d3bc0aadf08164b3ec"
FFMPEG_LIBS = ["libavcodec.so.61", "libavformat.so.61", "libavutil.so.59",
               "libswresample.so.5", "libswscale.so.8"]

# Development packages, plus the runtime packages that hold the .so files they link.
PACKAGES = [
    "libc6", "libc6-dev", "linux-libc-dev", "libcrypt1", "libcrypt-dev",
    "libgcc-s1", "libgcc-14-dev", "libstdc++6", "libstdc++-14-dev",
    "libvulkan1", "libvulkan-dev",
    "libavcodec61", "libavcodec-dev", "libavformat61", "libavformat-dev",
    "libavutil59", "libavutil-dev", "libswresample5", "libswresample-dev",
    "libswscale8", "libswscale-dev",
]
# SDL2 is real SDL2 in some suites and sdl2-compat in others; take the first present.
SDL_CHOICES = [("libsdl2-compat-dev", "libsdl2-compat"), ("libsdl2-dev", "libsdl2-2.0-0")]


def download(url):
    print(f"  {url}", flush=True)
    with urllib.request.urlopen(url) as response:
        return response.read()


def package_index():
    text = lzma.decompress(download(f"{MIRROR}/dists/{SUITE}/main/binary-amd64/Packages.xz")).decode()
    index = {}
    for stanza in text.split("\n\n"):
        fields = {}
        for line in stanza.splitlines():
            if line and not line[0].isspace() and ":" in line:
                key, value = line.split(":", 1)
                fields[key] = value.strip()
        if "Package" in fields:
            index[fields["Package"]] = fields
    return index


def ar_members(data):
    if data[:8] != b"!<arch>\n":
        raise ValueError("not an ar archive")
    offset = 8
    while offset < len(data):
        header = data[offset:offset + 60]
        name = header[:16].decode().strip().rstrip("/")
        size = int(header[48:58].decode().strip())
        yield name, data[offset + 60:offset + 60 + size]
        offset += 60 + size + (size & 1)


def open_data_tar(deb):
    for name, payload in ar_members(deb):
        if name.startswith("data.tar"):
            if name.endswith(".zst"):
                raise RuntimeError("zstd-compressed package; use an xz suite")
            return tarfile.open(fileobj=io.BytesIO(payload), mode="r:*")
    raise ValueError("package has no data.tar")


def safe_path(root, member_name):
    relative = os.path.normpath(member_name.lstrip("./").lstrip("/"))
    if relative.startswith("..") or os.path.isabs(relative):
        raise ValueError(f"unsafe path {member_name}")
    return os.path.join(root, relative)


def extract(deb, root, links):
    with open_data_tar(deb) as archive:
        for member in archive.getmembers():
            if member.name in (".", "./"):
                continue
            target = safe_path(root, member.name)
            if member.isdir():
                os.makedirs(target, exist_ok=True)
            elif member.issym() or member.islnk():
                links.append((member.name, member.linkname, member.issym()))
            elif member.isfile():
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with archive.extractfile(member) as source, open(target, "wb") as out:
                    shutil.copyfileobj(source, out)


def resolve_links(root, links):
    # Debian is merged-/usr: /lib and /lib64 are /usr/lib and /usr/lib64.
    def in_sysroot(path):
        path = path.replace("\\", "/")
        for prefix in ("/lib/", "/lib64/", "/bin/", "/sbin/"):
            if path.startswith(prefix):
                path = "/usr" + path
        return safe_path(root, path)

    pending = list(links)
    for _ in range(8):  # chains of links
        remaining = []
        for name, link, symbolic in pending:
            destination = safe_path(root, name)
            if symbolic and not link.startswith("/"):
                link = posixpath.join(posixpath.dirname("/" + name.lstrip("./")), link)
            source = in_sysroot(posixpath.normpath(link) if symbolic else "/" + link.lstrip("./"))
            if os.path.isfile(source):
                os.makedirs(os.path.dirname(destination), exist_ok=True)
                shutil.copyfile(source, destination)
            elif os.path.isdir(source):
                continue
            else:
                remaining.append((name, link, symbolic))
        if len(remaining) == len(pending):
            break
        pending = remaining
    return pending


def finish_layout(root):
    libdir = os.path.join(root, "usr", "lib", "x86_64-linux-gnu")
    # sdl2-compat keeps its library in a subdirectory; the linker looks in libdir.
    for name in ("libSDL2-2.0.so.0", "libSDL2-2.0.so", "libSDL2.so"):
        compat = os.path.join(libdir, "sdl2-compat", "libSDL2-2.0.so.0")
        if os.path.isfile(compat) and not os.path.exists(os.path.join(libdir, name)):
            shutil.copyfile(compat, os.path.join(libdir, name))
    # glibc's linker scripts name /lib/... and /lib64/...; merged /usr keeps them under usr/.
    # Rewrite the scripts instead of adding lib/ links, which clang would take as the GCC root.
    for name in os.listdir(libdir):
        path = os.path.join(libdir, name)
        if name.endswith(".so") and os.path.isfile(path) and os.path.getsize(path) < 4096:
            with open(path, "rb") as script:
                text = script.read()
            if text.startswith(b"/* GNU ld script"):
                text = text.replace(b" /lib/", b" /usr/lib/").replace(b" /lib64/", b" /usr/lib64/")
                with open(path, "wb") as script:
                    script.write(text)


def fetch_sysroot(root):
    print(f"Debian {SUITE} package index")
    index = package_index()
    wanted = list(PACKAGES)
    for dev, runtime in SDL_CHOICES:
        if dev in index:
            wanted += [dev, runtime]
            break
    else:
        raise RuntimeError("no SDL2 development package in the index")
    missing = [name for name in wanted if name not in index]
    if missing:
        raise RuntimeError(f"packages not in {SUITE}: {', '.join(missing)}")
    os.makedirs(root, exist_ok=True)
    for entry in os.listdir(root):  # empty it in place; the directory may be someone's cwd
        path = os.path.join(root, entry)
        shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
    links = []
    print(f"Sysroot ({len(wanted)} packages)")
    for name in wanted:
        extract(download(f"{MIRROR}/{index[name]['Filename']}"), root, links)
    unresolved = resolve_links(root, links)
    finish_layout(root)
    for name, link, _ in unresolved:
        # Sanitizer and OpenMP runtimes are unused; finish_layout may have supplied the rest.
        if not name.startswith("./usr/lib/gcc/") and not os.path.exists(safe_path(root, name)):
            print(f"  unresolved link {name} -> {link}")
    with open(os.path.join(root, "packages.txt"), "w") as manifest:
        for name in wanted:
            manifest.write(f"{name} {index[name]['Version']}\n")


def fetch_tools(tools):
    os.makedirs(tools, exist_ok=True)
    if platform.system() != "Windows":
        print("Not Windows: install glslangValidator and pkg-config from the system instead")
        return
    print("Host tools")
    glslang = f"glslang-{GLSLANG_VERSION}-windows-x86_64-release.zip"
    url = f"https://github.com/KhronosGroup/glslang/releases/download/{GLSLANG_VERSION}/{glslang}"
    with zipfile.ZipFile(io.BytesIO(download(url))) as archive:
        for entry in archive.namelist():
            if entry.endswith("bin/glslangValidator.exe") or entry.endswith("bin/glslang.exe"):
                with archive.open(entry) as source, open(os.path.join(tools, os.path.basename(entry)), "wb") as out:
                    shutil.copyfileobj(source, out)
    url = f"https://files.pythonhosted.org/packages/py3/p/pkgconf/{PKGCONF_WHEEL}"
    with zipfile.ZipFile(io.BytesIO(download(url))) as archive:
        with archive.open("pkgconf/.bin/pkgconf.exe") as source, open(os.path.join(tools, "pkgconf.exe"), "wb") as out:
            shutil.copyfileobj(source, out)


def fetch_deck_libs(deck_lib):
    print("FFmpeg libraries for the Deck")
    url = f"https://github.com/BtbN/FFmpeg-Builds/releases/download/{FFMPEG_RELEASE}/{FFMPEG_ASSET}"
    data = download(url)
    if hashlib.sha256(data).hexdigest() != FFMPEG_SHA256:
        raise RuntimeError(f"{FFMPEG_ASSET}: sha256 mismatch")
    os.makedirs(deck_lib, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:xz") as archive:
        members = {member.name.rsplit("/", 1)[-1]: member for member in archive.getmembers()}
        for name in FFMPEG_LIBS + ["LICENSE.txt"]:
            member = members[name]
            while member.issym():  # libavcodec.so.61 -> libavcodec.so.61.19.101
                member = members[member.linkname]
            out_name = "FFmpeg-LICENSE.txt" if name == "LICENSE.txt" else name
            with archive.extractfile(member) as source, open(os.path.join(deck_lib, out_name), "wb") as out:
                shutil.copyfileobj(source, out)


def main():
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=os.path.join(repo, "out", "linux-cross"))
    parser.add_argument("--force", action="store_true", help="fetch again even if present")
    args = parser.parse_args()
    root = os.path.join(args.out, "sysroot")
    tools = os.path.join(args.out, "tools")
    deck_lib = os.path.join(args.out, "deck-lib")
    if args.force or not os.path.isfile(os.path.join(root, "packages.txt")):
        fetch_sysroot(root)
    else:
        print(f"Sysroot present: {root}")
    if args.force or not os.path.isfile(os.path.join(tools, "pkgconf.exe")):
        fetch_tools(tools)
    else:
        print(f"Tools present: {tools}")
    if args.force or not all(os.path.isfile(os.path.join(deck_lib, name)) for name in FFMPEG_LIBS):
        fetch_deck_libs(deck_lib)
    else:
        print(f"Deck libraries present: {deck_lib}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
