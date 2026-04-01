# The Browser That Never Started

The browser feature worked fine in the container. Then we rebuilt the container and it stopped working. That's the most frustrating category of bug because the code didn't change—just the environment did. And the environment is opaque.

The surface symptom: users would click Connect on the `/browser` page and see "Starting browser..." indefinitely. The button stayed there. No error appeared. The API logs said `[RemoteBrowserService.start] Starting browser...` and then nothing. Patchright's `launchPersistentContext` just... never returned.

## The first diagnosis was wrong

When a browser binary can't be found, you get an obvious `ENOENT: no such file or directory` error. We had that. The setup uses Patchright, which downloads its own Chromium binary (`chromium_headless_shell`) during package installation. On Alpine Linux, Patchright downloads a glibc-compiled binary that cannot run on Alpine's musl C library. The binary exists on disk but fails to execute.

The fix is to symlink Patchright's expected binary path to Alpine's own Chromium, which is compiled for musl and works on Alpine. The Dockerfile creates that symlink:

```dockerfile
RUN apk add --no-cache ... chromium
RUN for dir in /root/.cache/ms-playwright/chromium_headless_shell-*/chrome-linux; do \
      ln -sf /usr/bin/chromium-browser "$dir/headless_shell"; \
    done
```

After this change, the `ENOENT` error went away. The browser still didn't start.

## The hypothesis about wrapper scripts

`/usr/bin/chromium-browser` on Alpine isn't the actual Chrome binary. It's a shell script:

```sh
#!/bin/sh
for f in /etc/chromium/*.conf; do [ -f "$f" ] && . "$f"; done
exec "$PROGDIR/chromium" ${CHROMIUM_FLAGS} "$@"
```

It sources `/etc/chromium/chromium.conf` (which adds `--ozone-platform-hint=auto`) and then `exec`s the real binary at `/usr/lib/chromium/chromium`, forwarding all arguments.

The theory was that this wrapper was interfering with Patchright's CDP pipe communication. Patchright uses `--remote-debugging-pipe` to communicate with Chrome via file descriptors 3 and 4. The wrapper forwards all arguments with `"$@"`, so the FDs should pass through. But maybe the extra flags from the wrapper were causing problems?

We tried setting `CHROMIUM_PATH` to point directly to the underlying binary, bypassing the wrapper entirely. The browser still didn't start.

We tried pointing it back to the wrapper. Still didn't start.

## The moment the actual error became visible

The problem with debugging a hanging process is that you can't see what it's doing. The API logs showed the browser starting and then went silent. We had no visibility into what was happening inside Chromium.

The fix was to run Patchright directly inside the container, without going through the API, with a short timeout:

```sh
docker exec dr_api bun -e "
const { chromium } = require('patchright');
chromium.launchPersistentContext('/tmp/test', {
  headless: true,
  args: ['--no-sandbox'],
  timeout: 10000
}).then(c => { console.log('SUCCESS'); return c.close(); })
  .catch(e => console.error('ERROR:', e.message));
"
```

When Patchright runs with a short timeout instead of an indefinite hang, it actually shows you the Chrome stderr output before timing out:

```
[pid=241][err] VK_KHR_surface: Extension not supported
[pid=241][err] EGL Driver message (Critical) eglInitialize: Internal Vulkan error (-7)
[pid=241][err] eglInitialize SwANGLE failed with error EGL_NOT_INITIALIZED
[pid=241][err] Exiting GPU process due to errors during initialization

[pid=272][err] VK_KHR_surface: Extension not supported
[pid=272][err] Exiting GPU process due to errors during initialization

[pid=322][err] VK_KHR_surface: Extension not supported
[pid=322][err] Exiting GPU process due to errors during initialization
```

That's Chromium repeatedly spawning a GPU process, the GPU process crashing, and Chromium spawning another one. Over and over. It never gives up, and it never becomes ready.

## What's actually happening

When you launch a modern Chrome browser—even in `--headless` mode—it starts a separate GPU process. This GPU process handles page rendering, compositing, and (in newer versions) screenshot capture. Chrome uses EGL or Vulkan to talk to the GPU hardware.

In a Docker container, there's no GPU hardware. There are no `/dev/dri` device files. There's no display server. EGL initialization fails. The GPU process crashes immediately. Chrome's main process sees that its GPU process died and starts a new one, expecting it to work this time. It doesn't. The cycle continues indefinitely because Chromium has no fallback logic for "GPU process never starts."

The whole time this is happening, Patchright is waiting for Chrome to signal that it's ready to accept DevTools commands. That signal never comes.

## The one-line fix

```typescript
const commonArgs = [
  '--disable-infobars',
  '--disable-blink-features=AutomationControlled',
  // ...other args...
  '--disable-gpu',
];
```

`--disable-gpu` tells Chrome not to start a GPU process at all. Instead of hardware-accelerated rendering, it falls back to Skia's CPU renderer. For headless use—screenshots, page rendering, CDP screencasting—this is completely fine. You don't notice the difference. The pages look the same, screenshots work the same, and the browser starts in about two seconds instead of hanging forever.

## Why this wasn't obvious earlier

The symptom was a hang, not a crash. When a process crashes, you get an error message. When a process hangs waiting for something that never comes, you get silence. Without explicitly running Patchright with a short timeout *inside the container* and watching the stderr output, the GPU crash loop was invisible—it was happening in a subprocess of the browser process, and the API was only logging what the TypeScript layer could observe.

The other thing that made this hard to pin down: the browser had worked before a container rebuild. The previous container had the Chromium symlink installed manually via `docker exec`. Manual changes to containers don't survive a rebuild. After a rebuild, the Dockerfile changes took effect, the symlink was there, but `--disable-gpu` was never a requirement before—or if it was, the previous environment happened to handle GPU initialization differently.

It's also the kind of flag that feels wrong to add. Disabling the GPU sounds like it would break rendering. It doesn't, because "GPU acceleration" and "rendering" aren't the same thing. Skia's CPU backend can render anything the GPU backend can render. It's just slower for animations and complex visual effects, neither of which matters for headless browser automation.

## The container-ready headless Chromium config

For headless Chromium in a container where GPU hardware isn't available:

```typescript
const args = [
  '--no-sandbox',              // Required in containers (no user namespace separation)
  '--disable-dev-shm-usage',   // /dev/shm is small in containers; use /tmp instead
  '--disable-gpu',             // No GPU hardware — prevents the crash loop
  '--headless',                // (Patchright handles this implicitly)
];
```

These three flags together produce a browser that starts reliably, renders correctly, and doesn't fight the container environment. The first two are well-known. The third one is the one people miss when they're working in containers for the first time, because the failure mode is a silent hang rather than an obvious error.

---

*Last updated: February 2026. Based on Chromium 131 / Alpine Linux / Patchright 1.57.0.*
