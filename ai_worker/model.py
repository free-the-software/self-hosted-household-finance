# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
"""
Which model the receipt reader uses, and fetching it. Kept apart from app.py
so the image's download step (Dockerfile) is rebuilt only when this file
changes, not with every change to the worker.

    python model.py      # downloads into MODEL_CACHE_DIR
"""
import os

MODEL_ID = 'florence-community/Florence-2-base'
# An exact commit, so the model cannot change underneath us
MODEL_REVISION = '00921df66db728a9ceb750f5eca43e5c203a2051'
CACHE_DIR = os.environ.get('MODEL_CACHE_DIR', '/opt/model')


def download():
    from transformers import AutoProcessor, Florence2ForConditionalGeneration
    for cls in (Florence2ForConditionalGeneration, AutoProcessor):
        cls.from_pretrained(MODEL_ID, revision=MODEL_REVISION, cache_dir=CACHE_DIR)


if __name__ == '__main__':
    download()
