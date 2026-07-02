# Jetson: USB Blu-ray/DVD kernel modules

> **Scope:** this is a **host-OS / Linux kernel** procedure, independent of
> Engram's own build. It gets a USB Blu-ray/DVD drive recognized as `/dev/sr0`
> so MakeMKV (and therefore Engram) can see it at all. It is **not** something
> that can be automated in Engram's release CI: kernel modules are tied via
> `vermagic` to the *exact* `uname -r` they were built against, so modules built
> on any machine other than the target Jetson — including a GitHub-hosted
> runner — are unusable there. Rebuild on each device instead of copying
> `.ko` files between Jetsons, even ones on the same JetPack version, unless
> `uname -r` is identical.
>
> **Validated on:** JetPack 6.2.2 / Jetson Linux R36.5.0, kernel
> `5.15.185-tegra`, by a community member. Different JetPack/kernel versions
> will need matching NVIDIA BSP downloads (see below) and may hit different
> `CONFIG_*` defaults.

## What this covers

- Holding NVIDIA L4T kernel/BSP packages before running `apt upgrade`, so a
  routine package update doesn't silently replace the kernel your custom
  modules are built for.
- Downloading the matching NVIDIA BSP and kernel source packages.
- Building `cdrom.ko`, `sr_mod.ko`, `sg.ko`, `udf.ko`, `isofs.ko`,
  `nls_utf8.ko`, and optionally `uas.ko`/`crc-itu-t.ko`.
- Installing, loading, and testing the modules against a real drive.
- A portable backup tarball, reusable only on another Jetson with the exact
  same `uname -r`.

## Required/optional kernel modules

| Module | Purpose | Notes |
|---|---|---|
| `cdrom.ko` | Generic CD/DVD-ROM class support | Build first if `CONFIG_CDROM=m`; other modules need its symbols. |
| `sr_mod.ko` | SCSI CD/DVD/BD block device support | Creates `/dev/sr0` for optical media. |
| `sg.ko` | SCSI generic access | Useful for `sg3_utils`, diagnostics, ripping/playback tools. |
| `udf.ko` | UDF filesystem | Required for most DVD and Blu-ray data discs. |
| `isofs.ko` | ISO9660 filesystem | Required for older CD/DVD data discs. |
| `nls_utf8.ko` | UTF-8 filename support | Commonly needed for readable filenames. |
| `uas.ko` | USB Attached SCSI | Only if `CONFIG_USB_UAS=m`. Skip if built in. |
| `usb-storage.ko` | USB mass storage | Often built in — don't force it as a module if `CONFIG_USB_STORAGE=y`. |
| `crc-itu-t.ko` | CRC dependency used by some filesystems | Only exists if `CONFIG_CRC_ITU_T=m`. Skip if built in. |

## Exact R36.5.0 download links

Use the links matching your exact release — for R36.5.0:

- Jetson Linux BSP: `https://developer.download.nvidia.com/embedded/L4T/r36_Release_v5.0/release/Jetson_Linux_R36.5.0_aarch64.tbz2`
- BSP sources: `https://developer.download.nvidia.com/embedded/L4T/r36_Release_v5.0/sources/public_sources.tbz2`
- Sample root filesystem: `https://developer.download.nvidia.com/embedded/L4T/r36_Release_v5.0/release/Tegra_Linux_Sample-Root-Filesystem_R36.5.0_aarch64.tbz2`
- Release SHA hashes: `https://developer.download.nvidia.com/embedded/L4T/r36_Release_v5.0/release/release_sha_hashes.txt`

## Part 1 — `apt upgrade` without changing the kernel

Run this before any package upgrade on a system whose custom optical-drive
modules must keep matching the running kernel exactly.

```bash
mkdir -p $HOME/apt-upgrade-no-kernel-backup
uname -a | tee $HOME/apt-upgrade-no-kernel-backup/uname-before.txt
uname -r | tee $HOME/apt-upgrade-no-kernel-backup/kernel-before.txt
dpkg -l | grep -E 'nvidia-l4t|linux-image|linux-headers|linux-modules' \
  | tee $HOME/apt-upgrade-no-kernel-backup/kernel-packages-before.txt
apt-mark showhold | tee $HOME/apt-upgrade-no-kernel-backup/holds-before.txt
```

Hold the Jetson kernel/BSP packages:

```bash
for pkg in nvidia-l4t-core nvidia-l4t-kernel nvidia-l4t-kernel-dtbs \
           nvidia-l4t-kernel-headers nvidia-l4t-kernel-oot-modules \
           nvidia-l4t-kernel-oot-headers nvidia-l4t-display-kernel \
           nvidia-l4t-bootloader nvidia-l4t-initrd nvidia-l4t-jetson-io; do
  if dpkg -s "$pkg" >/dev/null 2>&1; then
    echo "Holding $pkg"; sudo apt-mark hold "$pkg"
  else
    echo "Not installed, skipping $pkg"
  fi
done
apt-mark showhold | grep nvidia-l4t
```

Simulate, and only continue if nothing kernel-related would install:

```bash
sudo apt update
sudo apt -s upgrade | tee $HOME/apt-upgrade-no-kernel-backup/upgrade-simulation.txt
grep -Ei 'Inst nvidia-l4t-kernel|Inst nvidia-l4t-kernel-dtbs|Inst nvidia-l4t-kernel-headers|Inst nvidia-l4t-kernel-oot|Inst nvidia-l4t-display-kernel|Inst nvidia-l4t-bootloader|Inst nvidia-l4t-initrd|Inst linux-image|Inst linux-headers|Inst linux-modules' \
  $HOME/apt-upgrade-no-kernel-backup/upgrade-simulation.txt \
  || echo "No kernel installs found in simulation."
```

> Only continue if the last line printed is exactly `No kernel installs found
> in simulation.`

Then run the real upgrade (never `full-upgrade`/`dist-upgrade` here — those can
still pull in a kernel change) and confirm `uname -r` is unchanged before and
after:

```bash
sudo apt upgrade
echo "Before:"; cat $HOME/apt-upgrade-no-kernel-backup/kernel-before.txt
echo "After:"; uname -r
```

## Part 2 — Build the kernel modules

Build natively on the Jetson (avoids cross-compile toolchain mistakes).

### Install build tools

```bash
sudo apt update
sudo apt install -y build-essential bc flex bison libssl-dev libelf-dev \
  dwarves zstd git wget tar xz-utils kmod
sudo apt install -y nvidia-l4t-kernel-headers
export KREL="$(uname -r)"
```

### Download and unpack the BSP/sources

```bash
mkdir -p $HOME/jp622-r3650-build
cd $HOME/jp622-r3650-build
wget -O Jetson_Linux_R36.5.0_aarch64.tbz2 \
  https://developer.download.nvidia.com/embedded/L4T/r36_Release_v5.0/release/Jetson_Linux_R36.5.0_aarch64.tbz2
wget -O public_sources.tbz2 \
  https://developer.download.nvidia.com/embedded/L4T/r36_Release_v5.0/sources/public_sources.tbz2
tar xf Jetson_Linux_R36.5.0_aarch64.tbz2
tar xf public_sources.tbz2 -C $HOME/jp622-r3650-build
cd $HOME/jp622-r3650-build/Linux_for_Tegra/source
tar xf kernel_src.tbz2
tar xf kernel_oot_modules_src.tbz2
tar xf nvidia_kernel_display_driver_source.tbz2
```

### Prepare the source tree to match the running kernel exactly

```bash
cd $HOME/jp622-r3650-build/Linux_for_Tegra/source/kernel/kernel-jammy-src
export ARCH=arm64
export KDIR="$PWD"

if [ -r /proc/config.gz ]; then
  zcat /proc/config.gz > .config
elif [ -r "/boot/config-${KREL}" ]; then
  cp "/boot/config-${KREL}" .config
else
  echo "ERROR: could not find the running kernel config"; exit 1
fi

BASE="$(make -s ARCH=arm64 kernelversion)"
SUFFIX="${KREL#$BASE}"
scripts/config --set-str CONFIG_LOCALVERSION "$SUFFIX"
echo "" > .scmversion   # prevent a stray '+' from being appended
make ARCH=arm64 olddefconfig

echo "Running kernel: $KREL"
echo "Build kernel:   $(make -s ARCH=arm64 kernelrelease)"
```

> Both lines must read exactly the same kernel string before continuing.

### Configure optical-drive support without converting built-in features to modules

```bash
set_mod_if_not_builtin() {
  local sym="$1"
  if grep -q "^${sym}=y" .config; then
    echo "${sym} is already built in; leaving it built in."
  else
    scripts/config --module "$sym"
  fi
}
set_yes_if_not_module_or_builtin() {
  local sym="$1"
  if grep -q "^${sym}=y" .config; then
    echo "${sym} is already built in."
  elif grep -q "^${sym}=m" .config; then
    echo "${sym} is already a module."
  else
    scripts/config --enable "$sym"
  fi
}

set_mod_if_not_builtin CONFIG_CDROM
set_mod_if_not_builtin CONFIG_BLK_DEV_SR
set_mod_if_not_builtin CONFIG_CHR_DEV_SG
set_mod_if_not_builtin CONFIG_UDF_FS
set_mod_if_not_builtin CONFIG_ISO9660_FS
scripts/config --enable CONFIG_JOLIET
scripts/config --enable CONFIG_ZISOFS
set_mod_if_not_builtin CONFIG_NLS_UTF8
set_mod_if_not_builtin CONFIG_CRC_ITU_T
set_yes_if_not_module_or_builtin CONFIG_USB_STORAGE
set_mod_if_not_builtin CONFIG_USB_UAS

make ARCH=arm64 olddefconfig
```

### Prepare the tree and build each module

```bash
make ARCH=arm64 prepare
make ARCH=arm64 modules_prepare

SYM=""
if [ -f "/lib/modules/${KREL}/build/Module.symvers" ]; then
  SYM="/lib/modules/${KREL}/build/Module.symvers"
else
  SYM="$(find /usr/src -path "*${KREL}*" -name Module.symvers 2>/dev/null | head -n1)"
fi
[ -n "$SYM" ] && [ -f "$SYM" ] && cp "$SYM" "$KDIR/Module.symvers"

make ARCH=arm64 -j"$(nproc)" M=drivers/cdrom modules
EXTRA_SYMS=""
[ -f "$KDIR/drivers/cdrom/Module.symvers" ] && EXTRA_SYMS="$KDIR/drivers/cdrom/Module.symvers"
make ARCH=arm64 -j"$(nproc)" M=drivers/scsi KBUILD_EXTRA_SYMBOLS="$EXTRA_SYMS" modules
make ARCH=arm64 -j"$(nproc)" M=fs/udf KBUILD_EXTRA_SYMBOLS="$EXTRA_SYMS" modules
make ARCH=arm64 -j"$(nproc)" M=fs/isofs KBUILD_EXTRA_SYMBOLS="$EXTRA_SYMS" modules
make ARCH=arm64 -j"$(nproc)" M=fs/nls modules
make ARCH=arm64 -j"$(nproc)" M=drivers/usb/storage modules
make ARCH=arm64 -j"$(nproc)" M=lib modules
```

`cdrom.ko` won't exist if `CONFIG_CDROM=y` (built in) — that's fine. Same for
any module whose `CONFIG_*` ended up `=y` instead of `=m`.

### Verify vermagic before installing anything

```bash
for ko in drivers/cdrom/cdrom.ko drivers/scsi/sr_mod.ko drivers/scsi/sg.ko \
          fs/udf/udf.ko fs/isofs/isofs.ko fs/nls/nls_utf8.ko \
          drivers/usb/storage/uas.ko drivers/usb/storage/usb-storage.ko \
          lib/crc-itu-t.ko; do
  [ -f "$ko" ] && { echo; echo "$ko"; modinfo "$ko" | grep vermagic; }
done
```

> Every module that exists must report a vermagic starting with your `uname -r`
> (e.g. `5.15.185-tegra`). If it doesn't, `CONFIG_LOCALVERSION`/`.scmversion`
> weren't set correctly — redo the "prepare the source tree" step above.

### Install, load, and enable at boot

```bash
install_ko_if_exists() {
  [ -f "$1" ] && sudo install -D -m 0644 "$1" "$2" || echo "Skipping missing/built-in: $1"
}
install_ko_if_exists drivers/cdrom/cdrom.ko "/lib/modules/${KREL}/kernel/drivers/cdrom/cdrom.ko"
install_ko_if_exists drivers/scsi/sr_mod.ko "/lib/modules/${KREL}/kernel/drivers/scsi/sr_mod.ko"
install_ko_if_exists drivers/scsi/sg.ko "/lib/modules/${KREL}/kernel/drivers/scsi/sg.ko"
install_ko_if_exists fs/udf/udf.ko "/lib/modules/${KREL}/kernel/fs/udf/udf.ko"
install_ko_if_exists fs/isofs/isofs.ko "/lib/modules/${KREL}/kernel/fs/isofs/isofs.ko"
install_ko_if_exists fs/nls/nls_utf8.ko "/lib/modules/${KREL}/kernel/fs/nls/nls_utf8.ko"
install_ko_if_exists drivers/usb/storage/uas.ko "/lib/modules/${KREL}/kernel/drivers/usb/storage/uas.ko"
install_ko_if_exists drivers/usb/storage/usb-storage.ko "/lib/modules/${KREL}/kernel/drivers/usb/storage/usb-storage.ko"
install_ko_if_exists lib/crc-itu-t.ko "/lib/modules/${KREL}/kernel/lib/crc-itu-t.ko"
sudo depmod -a "$KREL"

sudo modprobe cdrom 2>/dev/null || true
sudo modprobe sr_mod
sudo modprobe sg
sudo modprobe udf
sudo modprobe isofs
sudo modprobe nls_utf8 2>/dev/null || true
sudo modprobe uas 2>/dev/null || true
sudo modprobe usb-storage 2>/dev/null || true
sudo modprobe crc-itu-t 2>/dev/null || true
lsmod | grep -E 'cdrom|sr_mod|sg|udf|isofs|nls_utf8|uas|crc_itu_t'

sudo tee /etc/modules-load.d/optical-drive.conf >/dev/null <<'EOF'
cdrom
sr_mod
sg
udf
isofs
nls_utf8
uas
usb_storage
crc_itu_t
EOF
# Remove uas from the autoload list if it isn't available as a module:
modinfo uas >/dev/null 2>&1 || sudo sed -i '/^uas$/d' /etc/modules-load.d/optical-drive.conf
# Same for usb_storage and crc_itu_t (both are commonly built into vmlinux):
modinfo usb_storage >/dev/null 2>&1 || sudo sed -i '/^usb_storage$/d' /etc/modules-load.d/optical-drive.conf
modinfo crc_itu_t >/dev/null 2>&1 || sudo sed -i '/^crc_itu_t$/d' /etc/modules-load.d/optical-drive.conf
```

## Test the drive

```bash
sudo apt install -y lsscsi sg3-utils udftools
# Plug in the Blu-ray/DVD drive, then:
lsusb; lsscsi; lsblk -f
ls -l /dev/sr* /dev/cdrom /dev/dvd /dev/sg* 2>/dev/null
dmesg | tail -100
```

You want to see `/dev/sr0`. Mount test:

```bash
sudo mkdir -p /mnt/bluray && sudo mount -t udf -o ro /dev/sr0 /mnt/bluray && ls -la /mnt/bluray
sudo umount /mnt/bluray
```

## Part 3 — Back up for reuse on the same kernel

Only restore this tarball onto another Jetson with the **exact same `uname -r`**
— for a different kernel, rebuild instead.

```bash
KREL="$(uname -r)"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$HOME/optical-kmods-${KREL}-${STAMP}"
mkdir -p "$BACKUP/modules" "$BACKUP/config-files"
for f in "/lib/modules/${KREL}/kernel/drivers/cdrom/cdrom.ko" \
         "/lib/modules/${KREL}/kernel/drivers/scsi/sr_mod.ko" \
         "/lib/modules/${KREL}/kernel/drivers/scsi/sg.ko" \
         "/lib/modules/${KREL}/kernel/fs/udf/udf.ko" \
         "/lib/modules/${KREL}/kernel/fs/isofs/isofs.ko" \
         "/lib/modules/${KREL}/kernel/fs/nls/nls_utf8.ko" \
         "/lib/modules/${KREL}/kernel/drivers/usb/storage/uas.ko" \
         "/lib/modules/${KREL}/kernel/drivers/usb/storage/usb-storage.ko" \
         "/lib/modules/${KREL}/kernel/lib/crc-itu-t.ko"; do
  [ -f "$f" ] && sudo cp --parents "$f" "$BACKUP/modules/"
done
[ -f /etc/modules-load.d/optical-drive.conf ] && \
  sudo cp --parents /etc/modules-load.d/optical-drive.conf "$BACKUP/config-files/"
tar -czf "${BACKUP}.tar.gz" -C "$(dirname "$BACKUP")" "$(basename "$BACKUP")"
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `grep: .config: no such file or directory` | `.config` was never copied into the kernel source tree. | Redo the config-copy step from inside `kernel-jammy-src`. |
| `invalid module format` | Module vermagic doesn't match `uname -r`. | Fix `CONFIG_LOCALVERSION`/`.scmversion`, rebuild, recheck `modinfo vermagic`. |
| `unknown symbol` such as `register_cdrom` | `sr_mod`/`udf`/`isofs` didn't know about `cdrom`'s `Module.symvers`. | Build `cdrom` first and pass `KBUILD_EXTRA_SYMBOLS` to dependent builds. |
| `exported twice` for `usb_storage` symbols | USB storage is already built into `vmlinux`. | Leave `CONFIG_USB_STORAGE=y`; don't force `usb-storage.ko`. |
| `unknown filesystem type udf` | `udf.ko` missing, not installed, or not loaded. | Install `udf.ko`, run `depmod`, `sudo modprobe udf`. |
| `/dev/sr0` never appears | `sr_mod`/USB/SCSI path not loaded or drive not detected. | Check `lsusb`, `lsscsi`, `dmesg`; `modprobe sr_mod sg uas` as applicable. |

## See also

- [Jetson CUDA GPU setup](./jetson.md) — the on-device step for GPU-accelerated
  ASR, a separate concern from this page.
