/**
 * Page-side helpers for the photo upload: start it in a Web Worker so it keeps going while the page is in a
 * background tab, and keep the browser from putting the page to sleep while it runs.
 */
import { runUpload, type UploadJob, type UploadResult } from "./upload-engine";

export type { UploadJob, UploadResult };

/**
 * Run the upload in a background worker. If a worker can't be started at all (very old browser, blocked script),
 * run it on the page's main thread instead, which works but is held up while the page is throttled.
 */
export function startUpload(job: UploadJob, onProgress: (fraction: number) => void): Promise<UploadResult> {
    // Note in the console when the tab or window changes state, next to the upload's own speed lines, so it is
    // clear whether a slowdown lines up with switching tabs or apps.
    let lastFraction = 0;
    const track = (fraction: number) => {
        lastFraction = fraction;
        onProgress(fraction);
    };
    const say = (what: string) => console.info(`[pixello] ${what} (${Math.round(lastFraction * 100)}% uploaded)`);
    const onVisibility = () => say(document.hidden ? "this tab went to the background" : "this tab is back in front");
    const onBlur = () => say("the browser window lost focus");
    const onFocus = () => say("the browser window has focus again");
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("blur", onBlur);
    window.addEventListener("focus", onFocus);
    return launchUpload(job, track).finally(() => {
        document.removeEventListener("visibilitychange", onVisibility);
        window.removeEventListener("blur", onBlur);
        window.removeEventListener("focus", onFocus);
    });
}

function launchUpload(job: UploadJob, onProgress: (fraction: number) => void): Promise<UploadResult> {
    // A worker has no page address to resolve a relative one like "/api" against, so hand it a full address
    job = { ...job, apiUrl: new URL(job.apiUrl, window.location.href).href.replace(/\/+$/, "") };
    const hooks = {
        progress: onProgress,
        warn: (message: string) => console.warn(message),
        info: (message: string) => console.info(message),
    };

    const onMainThread = (why: string) => {
        console.warn(`[pixello] upload is running on the page itself (${why}), so it may slow down in a background tab.`);
        return runUpload(job, hooks);
    };

    if (typeof Worker === "undefined") return onMainThread("background workers are not available");

    return new Promise((resolve, reject) => {
        let worker: Worker;
        try {
            worker = new Worker(new URL("./upload.worker.ts", import.meta.url));
        } catch (err) {
            onMainThread(`couldn't start the worker: ${err instanceof Error ? err.message : String(err)}`).then(resolve, reject);
            return;
        }

        let started = false;
        worker.onmessage = (e: MessageEvent<{ type: string; fraction?: number; result?: UploadResult; message?: string }>) => {
            const msg = e.data;
            if (msg.type === "started") {
                started = true;
                console.info("[pixello] upload is running in a background worker, so it keeps going in a background tab.");
            } else if (msg.type === "progress") {
                onProgress(msg.fraction ?? 0);
            } else if (msg.type === "done") {
                worker.terminate();
                resolve(msg.result as UploadResult);
            } else if (msg.type === "crashed") {
                worker.terminate();
                reject(new Error(`The upload stopped unexpectedly: ${msg.message}`));
            }
        };
        worker.onerror = (e) => {
            worker.terminate();
            if (!started) {
                // The worker script never ran (e.g. it failed to load): nothing was sent yet, so it is safe to run here
                onMainThread(`the worker failed to load: ${e.message}`).then(resolve, reject);
            } else {
                reject(new Error(`The upload stopped unexpectedly: ${e.message}`));
            }
        };
        worker.postMessage(job);
    });
}

/**
 * Keeps the browser from pausing the page while photos upload. Returns a function that undoes it.
 *
 * - A Web Lock: Chrome documents that a page holding one is not frozen when it sits in the background
 *   (the "Energy Saver" freezing that would otherwise stall the upload's network callbacks).
 * - A screen wake lock: stops the screen (and with it the computer) from going to sleep mid-upload while the
 *   page is on screen. It is dropped by the browser when the tab is hidden, so it is asked for again on return.
 *
 * Both are optional extras: browsers that don't have them simply skip them.
 */
export function keepUploadAlive(): () => void {
    let finished = false;
    const lock: { release: (() => void) | null } = { release: null };
    let wake: WakeLockSentinel | null = null;

    if (typeof navigator !== "undefined" && "locks" in navigator) {
        navigator.locks
            .request("picshare-upload", () => new Promise<void>((resolve) => {
                if (finished) resolve();
                else lock.release = resolve;
            }))
            .catch(() => {});
    }

    const getWakeLock = async () => {
        if (finished || typeof navigator === "undefined" || !("wakeLock" in navigator)) return;
        try {
            wake = await navigator.wakeLock.request("screen");
        } catch {
            // refused (hidden tab, battery saver): the upload still runs, the screen may just sleep
        }
    };
    const onVisible = () => {
        if (!document.hidden) getWakeLock();
    };
    getWakeLock();
    document.addEventListener("visibilitychange", onVisible);

    return () => {
        finished = true;
        lock.release?.();
        wake?.release().catch(() => {});
        document.removeEventListener("visibilitychange", onVisible);
    };
}
