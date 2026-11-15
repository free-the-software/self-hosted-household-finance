# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
"""
Receipt OCR worker: reads the text on a receipt image, with the position of
each piece of text, using the Florence-2 vision model on the CPU.

Uses the Florence-2 support built into transformers and a checkpoint in the
safetensors format, so no code is downloaded from the model repository and
run (no trust_remote_code).

The model is downloaded when the image is built (model.py), so the worker never goes online. It is loaded into memory when
a receipt arrives and released after MODEL_IDLE_MINUTES without one, so the
worker holds about 2 GB only while it has receipts to read.
"""
import ctypes
import gc
import io
import logging
import os
import threading
import time

from flask import Flask, jsonify, request
from PIL import Image
from transformers import AutoProcessor, Florence2ForConditionalGeneration

from model import CACHE_DIR, MODEL_ID, MODEL_REVISION

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger('ai_worker')

app = Flask(__name__)

# Released from memory after this long with nothing to read (0 = keep it loaded)
IDLE_MINUTES = float(os.environ.get('MODEL_IDLE_MINUTES', 10))
# Largest image side, in pixels, accepted for processing
MAX_IMAGE_SIDE = 12000
PROMPT = "<OCR_WITH_REGION>"


def _load(cls):
    """From the copy made at build time only; never goes online."""
    return cls.from_pretrained(MODEL_ID, revision=MODEL_REVISION, cache_dir=CACHE_DIR, local_files_only=True)


_lock = threading.Lock()
_loaded = None          # (model, processor) while in memory
_last_used = 0.0


def _model():
    """The model and processor, loaded now if they aren't in memory."""
    global _loaded, _last_used
    with _lock:
        if _loaded is None:
            started = time.monotonic()
            logger.info("Loading Florence-2 into memory...")
            model = _load(Florence2ForConditionalGeneration)
            model.eval()
            _loaded = (model, _load(AutoProcessor))
            logger.info("Loaded in %.0f seconds", time.monotonic() - started)
        _last_used = time.monotonic()
        return _loaded


def _release_when_idle():
    """Background: gives the memory back after IDLE_MINUTES with nothing to read."""
    global _loaded
    while True:
        time.sleep(min(30, IDLE_MINUTES * 60 / 2))
        with _lock:
            if _loaded is None or time.monotonic() - _last_used < IDLE_MINUTES * 60:
                continue
            _loaded = None
        gc.collect()
        try:
            ctypes.CDLL('libc.so.6').malloc_trim(0)    # hand freed memory back to the system
        except (OSError, AttributeError):
            pass
        logger.info("Released the model after %g minutes without a receipt", IDLE_MINUTES)


if IDLE_MINUTES > 0:
    threading.Thread(target=_release_when_idle, name='release-when-idle', daemon=True).start()


@app.route('/parse', methods=['POST'])
def parse_receipt():
    """Extracts text WITH precise X/Y bounding boxes."""
    global _last_used
    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    try:
        image = Image.open(io.BytesIO(request.files['file'].read()))
        if max(image.size) > MAX_IMAGE_SIDE:
            return jsonify({"error": "Image is too large"}), 400
        image = image.convert("RGB")
    except Exception:
        return jsonify({"error": "Invalid image"}), 400

    try:
        model, processor = _model()
        inputs = processor(text=PROMPT, images=image, return_tensors="pt")
        generated_ids = model.generate(
            input_ids=inputs["input_ids"],
            pixel_values=inputs["pixel_values"],
            max_new_tokens=1024,
            do_sample=False,
            num_beams=3
        )

        # Keep the special tokens: the <loc_N> tags carry each text's position.
        generated_text = processor.batch_decode(generated_ids, skip_special_tokens=False)[0]
        # Strip only the start/end/padding tokens and the task prompt, so the
        # caller gets "text<loc_..>...<loc_..>text<loc_..>..." and nothing else.
        clean_raw_text = generated_text.replace("<s>", "").replace(PROMPT, "").replace("</s>", "").replace("<pad>", "")

        _last_used = time.monotonic()      # idle time counts from the end of the last receipt
        return jsonify({"text": clean_raw_text.strip()})

    except Exception:
        # The detail goes to this container's log, not to the caller
        logger.exception("Reading the image failed")
        return jsonify({"error": "The image could not be read"}), 500


@app.route('/health', methods=['GET'])
def health():
    # Ready whether or not the model is in memory: it loads on the next receipt
    return jsonify({"status": "ready", "model_loaded": _loaded is not None})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
