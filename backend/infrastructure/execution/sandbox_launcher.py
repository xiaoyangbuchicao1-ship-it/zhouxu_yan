"""Trusted single-threaded launcher: rlimits then Bubblewrap + libseccomp.

Executed as an isolated Python process, never through preexec_fn in the
multithreaded API. No application configuration or credentials are imported.
"""

import ctypes
import ctypes.util
import errno
import os
import resource
import sys


class _ArgCompare(ctypes.Structure):
    _fields_ = [("arg", ctypes.c_uint), ("op", ctypes.c_int),
                ("datum_a", ctypes.c_uint64), ("datum_b", ctypes.c_uint64)]


def _command_filter() -> int:
    """Compile a libseccomp BPF filter for Bubblewrap's final command only."""
    library = ctypes.util.find_library("seccomp")
    if library is None:
        raise RuntimeError("libseccomp is required for isolated execution")
    seccomp = ctypes.CDLL(library, use_errno=True)
    seccomp.seccomp_init.argtypes = [ctypes.c_uint32]
    seccomp.seccomp_init.restype = ctypes.c_void_p
    seccomp.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    seccomp.seccomp_syscall_resolve_name.restype = ctypes.c_int
    seccomp.seccomp_rule_add_array.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int,
                                             ctypes.c_uint, ctypes.POINTER(_ArgCompare)]
    seccomp.seccomp_export_bpf.argtypes = [ctypes.c_void_p, ctypes.c_int]
    seccomp.seccomp_release.argtypes = [ctypes.c_void_p]
    context = seccomp.seccomp_init(0x7FFF0000)  # SCMP_ACT_ALLOW; Docker is the outer allowlist.
    if not context:
        raise RuntimeError("cannot allocate execution seccomp filter")
    descriptor = os.memfd_create("zhouxu-command-filter", 0)
    try:
        def deny(name: str, error: int = errno.EPERM, compare: _ArgCompare | None = None):
            number = seccomp.seccomp_syscall_resolve_name(name.encode())
            if number < 0:
                raise RuntimeError(f"unsupported execution syscall: {name}")
            rule = ctypes.pointer(compare) if compare is not None else None
            result = seccomp.seccomp_rule_add_array(context, 0x50000 | error, number,
                                                   int(compare is not None), rule)
            if result != 0:
                raise OSError(-result, f"cannot restrict {name}")

        for syscall in ("unshare", "setns", "mount", "umount2", "pivot_root", "chroot",
                        "ptrace", "process_vm_readv", "process_vm_writev", "pidfd_getfd"):
            deny(syscall)
        # libc falls back to clone on ENOSYS. Ordinary subprocesses and
        # threads still work; clone's namespace flags do not.
        deny("clone3", errno.ENOSYS)
        for flag in (0x00020000, 0x02000000, 0x04000000, 0x08000000,
                     0x10000000, 0x20000000, 0x40000000):
            deny("clone", compare=_ArgCompare(0, 7, flag, flag))
        # AF_UNIX supports local office tools; other socket families are
        # denied in addition to the isolated network namespace.
        deny("socket", compare=_ArgCompare(0, 1, 1, 0))
        deny("socketpair", compare=_ArgCompare(0, 1, 1, 0))
        result = seccomp.seccomp_export_bpf(context, descriptor)
        if result != 0:
            raise OSError(-result, "cannot export execution filter")
        os.lseek(descriptor, 0, os.SEEK_SET)
        os.set_inheritable(descriptor, True)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise
    finally:
        seccomp.seccomp_release(context)


def main() -> None:
    memory, cpu, pids, file_bytes = map(int, sys.argv[1:5])
    for kind, limit in ((resource.RLIMIT_AS, memory), (resource.RLIMIT_CPU, cpu),
                        (resource.RLIMIT_NPROC, pids), (resource.RLIMIT_FSIZE, file_bytes),
                        (resource.RLIMIT_CORE, 0)):
        resource.setrlimit(kind, (limit, limit))
    descriptor = _command_filter()
    command = sys.argv[5:]
    command[1:1] = ["--seccomp", str(descriptor)]
    os.execvpe(command[0], command, os.environ)


if __name__ == "__main__":
    main()
