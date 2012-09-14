#!/bin/sh -e

#Dell factory GRUB2 binary builder
#Creates binaries for use within Dell factory process

#This script can be easily ran on a development system by modifying some
#environment variables for source and target directories.
#
# TARGET specifies where the binaries will end up
# PATCHES specifies where the patches
# GRUB_SRC specifies where to find a grub source tree containing a debian/
#          directory (including a collection of distro patches)
# LEGACY_GRUBDIR specifies where legacy GRUB2  (i386-pc) files are stored
# UEFI_GRUBDIR specifies where uEFI GRUB2  (x86_64-efi) files are stored
# ISO_LOADER specifies the directory where the i386-pc ISO loader files
#            will be stored
# SOURCE_GRUBDIR is where the conf files used are stored
# TARGET_GRUBCFG is where the main grubcfg will be placed after modification

[ -n "$TARGET" ]         || TARGET=/var/lib/dell-recovery
[ -n "$LEGACY_GRUBDIR" ] || LEGACY_GRUBDIR=/usr/lib/grub/i386-pc
[ -n "$UEFI_GRUBDIR" ]   || UEFI_GRUBDIR=/usr/lib/grub/x86_64-efi
[ -n "$SOURCE_GRUBDIR" ] || SOURCE_GRUBDIR=/usr/share/dell/grub
[ -n "$ISO_LOADER" ]     || ISO_LOADER=$TARGET/iso/i386-pc
[ -n "$TARGET_GRUBCFG" ] || TARGET_GRUBCFG=$TARGET/grub.cfg
if [ -z "$PATCHES" ]; then
    RELEASE=$(lsb_release -sc)
    [ ! -d $SOURCE_GRUBDIR/patches/$RELEASE ] && RELEASE=trunk
    PATCHES=$SOURCE_GRUBDIR/patches/$RELEASE
fi
mkdir -p $TARGET

common_modules="loadenv part_gpt fat ntfs ext2 ntfscomp search linux boot \
                minicmd cat cpuid chain halt help ls reboot echo test     \
                configfile sleep keystatus normal true font"

#x86_64-efi factory bootloader, EFI target.  requires grub-efi-amd64-bin
if [ -d $UEFI_GRUBDIR ] &&
   [ ! -f $TARGET/grubx64.efi ]; then
    echo "Building bootloader images for x86_64-efi"
    efi_modules="efi_uga efi_gop gfxterm part_gpt"
    grub-mkimage -c $SOURCE_GRUBDIR/embedded.cfg \
                 --prefix=/factory                    \
                 -o $TARGET/grubx64.efi -O x86_64-efi \
                 $common_modules $efi_modules
fi

#i386-pc factory bootloader, legacy target.  reguires grub-pc-bin
if [ -d $LEGACY_GRUBDIR ] &&
   [ ! -f $TARGET/core.img ]; then
    echo "Building bootloader images for i386-pc"
    x86_modules="biosdisk part_msdos vbe vga vga_text"
    #build core image
    grub-mkimage -c $SOURCE_GRUBDIR/embedded.cfg \
                 --prefix=/factory                    \
                 -o $TARGET/core.img -O i386-pc       \
                 $common_modules $x86_modules
    #copy boot.img
    cat /usr/lib/grub/i386-pc/boot.img > $TARGET/boot.img
fi

#generate grub.cfg used for factory bootloaders
if [ ! -c $TARGET_GRUBCFG ]; then
    echo "Creating factory grub.cfg"
    OS=$(lsb_release -s -d)
    sed "s,#OS#,$OS,; /#UUID#/d" \
        $SOURCE_GRUBDIR/recovery_partition.cfg \
        > $TARGET_GRUBCFG
fi

#i386 ISO/USB legacy bootloader. requires grub-pc-bin
if [ -d $LEGACY_GRUBDIR ] &&
   [ ! -c $ISO_LOADER ] &&
   [ ! -d $ISO_LOADER ]; then
    echo "Building bootloader images for i386-pc DVD/USB boot"
    mkdir -p $ISO_LOADER
    #common
    cp $LEGACY_GRUBDIR/*.mod $ISO_LOADER
    cp $LEGACY_GRUBDIR/*.lst $ISO_LOADER
    cp $LEGACY_GRUBDIR/efiemu??.o $ISO_LOADER
    #eltorito
    cp $LEGACY_GRUBDIR/cdboot.img $ISO_LOADER
    #usb creator
    cp $LEGACY_GRUBDIR/boot.img $ISO_LOADER
    workdir="$(mktemp -d workdir-image.XXXXXX)"
    mkdir -p "$workdir"
###TODO###
##
#fix up to search casper-uuid or casper-uuid-generic
#depending upon situation somehow
##
##########
    cat >"$workdir/grub.cfg" <<EOF
search.file /.disk/casper-uuid root
set prefix=(\$root)/boot/grub/i386-pc
source \$prefix/grub.cfg
EOF

    #core.img
    grub-mkimage -c "$workdir/grub.cfg" \
                 -p '/boot/grub/i386-pc' \
                 -o $ISO_LOADER/core.img \
                 -O i386-pc \
                 search_fs_file biosdisk iso9660 part_msdos fat
    #eltorito
    cat $ISO_LOADER/cdboot.img $ISO_LOADER/core.img > $ISO_LOADER/../eltorito.img

    #cleanup
    rm -rf $workdir
fi

#grub-setup.exe
if [ -d /usr/lib/gcc/i586-mingw32msvc ] &&
   [ -d $PATCHES ] &&
   [ -x /usr/bin/quilt ] &&
   [ -x /usr/bin/autogen ] &&
   [ -x /usr/bin/autoreconf ] &&
   [ -x /usr/bin/libtoolize ] &&
   [ -x /usr/bin/bison ] &&
   [ -x /usr/bin/flex ] &&
   [ -x /usr/bin/dpkg-source ] &&
   [ ! -f $TARGET/grub-setup.exe ]; then
    echo "Building bootloader installer for mingw32 ($RELEASE)"
    BUILD_DIR=$(mktemp -d)
    cd $BUILD_DIR
    if [ -n "$GRUB_SRC" ]; then
        cp -R $GRUB_SRC .
    else
        apt-get source -qq grub2
    fi
    cd grub2*
    for item in $(ls $PATCHES); do
        echo $item >> debian/patches/series
        cp -f $PATCHES/$item debian/patches
    done
    QUILT_PATCHES=debian/patches quilt push -a -q
    ./autogen.sh >/dev/null 2>&1
    CC=i586-mingw32msvc-gcc ./configure --host=i586-mingw32msvc --disable-efiemu>/dev/null
    cd grub-core/gnulib && make > /dev/null && cd ../..
    make grub_script.tab.h grub_script.yy.h grub-bios-setup.exe >/dev/null
    cp grub-bios-setup.exe $TARGET
    rm -rf $BUILD_DIR
fi
