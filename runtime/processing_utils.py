"""Minimal image processing utilities for Gemi_MCP."""

from PIL import Image, PngImagePlugin

_shared_processor = None


def save_with_metadata(processed_img, original_img, save_path, extra_meta=None):
    """Save processed_img as PNG, embedding extra_meta as PNG text chunks."""
    meta = PngImagePlugin.PngInfo()
    if extra_meta:
        for k, v in extra_meta.items():
            if v:
                meta.add_text(str(k), str(v))
    processed_img.save(save_path, format="PNG", pnginfo=meta)


class _IdentityProcessor:
    def hybrid_process(self, img):
        return img


def get_shared_processor(use_gpu=False):
    global _shared_processor
    if _shared_processor is None:
        _shared_processor = _IdentityProcessor()
    return _shared_processor
