"""_warmup_vlm — extracted from automation/main.py."""
import time as _time


def _warmup_vlm(vlm) -> bool:
    """G1: Trigger VLM endpoint load before the scenario loop.

    llama-swap returns 502 while swapping models; the failover wrapper retries
    with backoff, consuming ~90s on a cold endpoint. Running a tiny warmup call
    here absorbs that wait outside the scenario step budget.

    Returns True if warmup succeeded, False if it timed out or errored (non-fatal).
    """
    from PIL import Image as _Image

    print("[mOSdat] Warming up VLM endpoint (model swap may take 30-60s)...")
    t0 = _time.perf_counter()
    try:
        img = _Image.new("RGB", (64, 64), (0, 0, 0))
        result = vlm.verify(img, "is the screen visible")
        elapsed = _time.perf_counter() - t0
        print(f"[mOSdat]   Warmup OK in {elapsed:.1f}s (answer={'yes' if result else 'no'})")
        return True
    except Exception as e:
        elapsed = _time.perf_counter() - t0
        print(f"[mOSdat]   Warmup failed after {elapsed:.1f}s: {e}")
        print("[mOSdat]   Proceeding anyway — scenario may take longer for first VLM call")
        return False
