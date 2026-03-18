import sys
import subprocess
import re

class HostOSPackage:
    """ Maps host package/host components to their respective OS package names  """

    qemu={}
    qemu["fedora"]="qemu"
    qemu["ubuntu"]="qemu-system"
    qemu["debian"]="qemu-system"
    qemu["centos"]="qemu-kvm-core"
    qemu["rocky"]="qemu-kvm-core"

    ovmf={}
    ovmf["fedora"]="edk2-ovmf"
    ovmf["ubuntu"]="ovmf"
    ovmf["debian"]="ovmf"
    ovmf["centos"]="edk2-ovmf"
    ovmf["rocky"]="edk2-ovmf"

