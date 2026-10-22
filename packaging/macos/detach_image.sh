#!/bin/bash
# Detach a disk image by its device (/dev/diskN), retrying while macOS keeps it
# busy. A detach can unmount the volume yet fail to eject the disk ("Resource
# busy"); detaching again by mount point then fails, which broke the 0.12.1
# build. The device stays valid until it's gone, and gone counts as done.
set -u
device="$1"
attached() { hdiutil info | grep -qw "$device"; }
for _ in 1 2 3 4 5 6; do
  attached || exit 0
  hdiutil detach "$device" || hdiutil detach -force "$device" || sleep 3
done
! attached
