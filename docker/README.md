# Chromium sandbox profile

`chromium-seccomp.json` is the Playwright Docker seccomp profile from
[microsoft/playwright at ae935a43d9e376e4759548f6b3c6905c7b282333](https://github.com/microsoft/playwright/blob/ae935a43d9e376e4759548f6b3c6905c7b282333/utils/docker/seccomp_profile.json),
licensed under [Apache 2.0](LICENSE.playwright).

It permits the `clone`, `setns` and `unshare` calls Chromium needs for its
unprivileged user namespace sandbox. Our additions return `ENOSYS` for `clone3`
so modern glibc falls back to `clone`, and allow `chroot` even when the outer
container has dropped `CAP_SYS_CHROOT`. Chromium needs that call inside its own
user namespace; kernel capability checks still apply. All other upstream rules
are unchanged.
The container runs as a non-root user with capabilities dropped; the application
continues to launch Chromium with `chromium_sandbox=True`.
