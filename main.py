#!/usr/bin/env python3
"""
Gemini Trader — AI screen trading assistant powered by Google Gemini Flash.

Usage:
    python main.py                              # full screen, 30s interval
    python main.py --region                     # drag-select a chart region
    python main.py --interval 15                # analyze every 15 seconds
    python main.py --model gemini-2.0-flash-lite  # cheaper/faster model
"""

import os
import sys
import argparse
import logging
import queue
import threading
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def _worker(
    result_q: queue.Queue,
    stop_ev: threading.Event,
    pause_ev: threading.Event,
    manual_ev: threading.Event,
    prompt_q: queue.Queue,
    region,
    api_key: str,
    model: str,
    interval: int,
) -> None:
    from capture import capture_screen
    from analyzer import analyze_screenshot
    from markets import open_market_summary

    history: list = []
    queued_prompt: str | None = None   # prompt that arrived during a wait/pause

    while not stop_ev.is_set():

        # ── Respect pause (but wake on manual trigger or user prompt) ─── #
        while pause_ev.is_set() and not stop_ev.is_set():
            if manual_ev.wait(timeout=0.5):
                manual_ev.clear()
                break
            try:
                queued_prompt = prompt_q.get_nowait()
                break
            except queue.Empty:
                continue

        if stop_ev.is_set():
            break

        # ── Grab any pending user prompt ─────────────────────────────── #
        user_prompt = queued_prompt
        queued_prompt = None
        if user_prompt is None:
            try:
                user_prompt = prompt_q.get_nowait()
            except queue.Empty:
                pass

        # ── Capture + analyze ────────────────────────────────────────── #
        result_q.put(("analyzing", None))
        try:
            log.info("Capturing screen…")
            img = capture_screen(region)

            market_ctx = open_market_summary()
            log.info(f"Sending to {model}…  market={market_ctx[:40]}")

            result, history = analyze_screenshot(
                img, api_key, model,
                history=history,
                user_prompt=user_prompt,
                market_context=market_ctx,
            )
            result_q.put(("result", result))
            log.info(f"Signal: {result.signal}  ({result.confidence})")
            log.info(f"Raw: {result.raw_response[:300]}")

        except Exception as exc:
            log.error(f"Analysis failed: {exc}")
            result_q.put(("error", str(exc)))

        # ── Wait for next interval (interruptible) ───────────────────── #
        deadline = time.monotonic() + interval
        while time.monotonic() < deadline and not stop_ev.is_set():
            if manual_ev.wait(timeout=0.3):
                manual_ev.clear()
                break
            try:
                queued_prompt = prompt_q.get_nowait()
                manual_ev.clear()
                break
            except queue.Empty:
                pass
            if pause_ev.is_set():
                break


def main() -> None:
    ap = argparse.ArgumentParser(
        description="AI trading assistant powered by Google Gemini Flash vision",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--interval", "-i", type=int, default=30, metavar="SEC",
                    help="Seconds between automatic analyses (default: 30)")
    ap.add_argument("--region", "-r", action="store_true",
                    help="Drag-select a chart region to monitor")
    ap.add_argument("--model", "-m", default="gemini-2.5-flash",
                    help="Gemini model ID (default: gemini-2.5-flash)")
    args = ap.parse_args()

    api_key = os.environ.get("GOOGLE_API_KEY", "")
    if not api_key:
        sys.exit(
            "\nError: GOOGLE_API_KEY is not set.\n"
            "  export GOOGLE_API_KEY='AIza...'\n"
            "  Get a free key at: https://aistudio.google.com/apikey\n"
        )

    region = None
    if args.region:
        from capture import select_region
        print("Draw a rectangle around the chart area you want to monitor.")
        print("Press Escape to fall back to full screen.\n")
        region = select_region()
        if region:
            log.info(f"Monitoring region: {region}")
        else:
            log.info("No region selected — monitoring full primary monitor.")

    print(
        f"\n  Model:    {args.model}\n"
        f"  Interval: {args.interval}s\n"
        f"  Region:   {region or 'full primary monitor'}\n"
        f"\n  Overlay appears top-right. Drag to reposition. Click ✕ or Ctrl-C to quit.\n"
    )

    result_q:  queue.Queue = queue.Queue()
    prompt_q:  queue.Queue = queue.Queue()
    stop_ev   = threading.Event()
    pause_ev  = threading.Event()
    manual_ev = threading.Event()

    threading.Thread(
        target=_worker,
        args=(result_q, stop_ev, pause_ev, manual_ev, prompt_q,
              region, api_key, args.model, args.interval),
        daemon=True,
    ).start()

    from overlay import TradingOverlay
    ov = TradingOverlay(result_q, stop_ev, pause_ev, manual_ev, prompt_q, args.interval)

    try:
        ov.run()
    except KeyboardInterrupt:
        pass
    finally:
        stop_ev.set()
        log.info("Stopped.")


if __name__ == "__main__":
    main()
