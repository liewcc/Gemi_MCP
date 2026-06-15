import asyncio
import sys
import os
from typing import Optional

import httpx
from mcp.server.fastmcp import FastMCP

ENGINE_URL = os.environ.get("GEMI_ENGINE_URL", "http://127.0.0.1:18800")

mcp = FastMCP("gemi-mcp")


async def _post(path: str, payload: dict | list | None = None) -> dict:
    """POST to engine_service and return parsed JSON."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{ENGINE_URL}{path}",
            json=payload,
            timeout=300.0,
        )
        resp.raise_for_status()
        return resp.json()


# ── 1. Model / Tool Selection ─────────────────────────────────────────────────

@mcp.tool()
async def apply_settings(model: Optional[str] = None, tool: Optional[str] = None) -> str:
    """Switch Gemini to a specific model and/or tool before generating.

    Call this before attaching files or submitting a prompt when you need a
    particular model (e.g. "2.0 Flash", "2.5 Pro") or tool (e.g. "Image
    generation", "Google Search").  Both parameters are optional — pass only
    the one(s) you want to change.

    Args:
        model: Display name of the Gemini model to select (partial match ok).
        tool:  Display name of the Gemini tool to enable (partial match ok).
               Pass "default" to leave the tool unchanged.

    Returns:
        Confirmation string or error description.
    """
    data = await _post("/browser/apply_settings", {"model": model, "tool": tool})
    if data.get("status") == "success":
        parts = []
        if model:
            parts.append(f"model={model}")
        if tool:
            parts.append(f"tool={tool}")
        return f"Settings applied: {', '.join(parts)}" if parts else "No changes requested."
    raise RuntimeError(data.get("message", "apply_settings failed"))


# ── 2. File Attachment ────────────────────────────────────────────────────────

@mcp.tool()
async def attach_files(file_paths: list[str]) -> str:
    """Attach one or more local files to the current Gemini prompt input.

    Uses smart incremental sync: files already attached are kept, missing ones
    are added, and extras are removed — minimising redundant uploads.

    Args:
        file_paths: Absolute paths to the files to attach.
                    Pass an empty list [] to clear all attachments.

    Returns:
        Summary of how many files were added / removed.
    """
    data = await _post("/browser/attach_files", file_paths)
    if data.get("status") == "success":
        return (
            f"Attachments synced: +{data.get('added', 0)} added, "
            f"-{data.get('removed', 0)} removed, "
            f"{data.get('total_now', 0)} total."
        )
    raise RuntimeError(data.get("message", "attach_files failed"))


# ── 3. Prompt Input ───────────────────────────────────────────────────────────

@mcp.tool()
async def set_prompt(text: str) -> str:
    """Type a prompt into Gemini's input box without submitting it.

    Use this to stage a prompt before calling submit_response, or to
    pre-fill text before attaching files.

    Args:
        text: The prompt text to place in the Gemini input field.

    Returns:
        Confirmation that the prompt was filled.
    """
    data = await _post("/browser/prompt", {"text": text})
    return f"Prompt staged: {str(data)}"


# ── 4. Submit & Monitor ───────────────────────────────────────────────────────

@mcp.tool()
async def submit_response(prompt: Optional[str] = None) -> str:
    """Submit the current prompt to Gemini and wait for an image response.

    Monitors the DOM until Gemini finishes generating, then returns whether an
    image was produced, the response was refused, quota was exceeded, etc.

    Call attach_files and set_prompt first if needed, then call this tool.
    Alternatively pass `prompt` here to type and submit in one step.

    Args:
        prompt: Optional prompt text to inject and submit in one shot.
                If None, submits whatever is already in the input box.

    Returns:
        Result status and message from the Gemini response monitor.
    """
    data = await _post("/browser/submit", {"text": prompt} if prompt else None)
    status = data.get("status", "unknown")
    message = data.get("message", "")
    return f"[{status}] {message}" if message else f"[{status}]"


# ── 5. Download Images ────────────────────────────────────────────────────────

@mcp.tool()
async def download_images(
    save_dir: str,
    prefix: str = "img",
    padding: int = 4,
    start: int = 1,
) -> str:
    """Download generated images from the last Gemini response to disk.

    Opens each image's lightbox dialog, clicks the download button (falling
    back to canvas/blob extraction when needed), deduplicates via perceptual
    hash, and saves numbered PNG files.

    Call this after submit_response returns a success status.

    Args:
        save_dir: Absolute path to the folder where images will be saved.
        prefix:   Filename prefix, e.g. "img" → img0001.png, img0002.png …
        padding:  Zero-padding width for the numeric counter (default 4).
        start:    Starting number for the counter (default 1).
                  The engine auto-tracks the max existing number in save_dir
                  when track_last_file_num is enabled in config.

    Returns:
        Number of images saved and their file paths.
    """
    payload = {
        "save_dir": save_dir,
        "naming": {"prefix": prefix, "padding": padding, "start": start},
        "meta": {},
    }
    data = await _post("/browser/download", payload)
    status = data.get("status")
    if status == "success":
        paths = data.get("saved_paths", [])
        return f"Downloaded {data.get('count', 0)} image(s): {paths}"
    if status == "ignored":
        return f"No images found to download: {data.get('message', '')}"
    raise RuntimeError(data.get("message", "download_images failed"))


# ── 6. Redo / Regenerate ──────────────────────────────────────────────────────

@mcp.tool()
async def redo_response() -> str:
    """Trigger Gemini's Redo (regenerate) action on the last response.

    Clicks the refresh/redo button and monitors until Gemini finishes
    regenerating.  Use this after a refused or unsatisfactory result before
    calling download_images again.

    Returns:
        Confirmation that the redo action was triggered.
    """
    data = await _post("/browser/redo")
    status = data.get("status", "unknown")
    message = data.get("message", "")
    if status == "success":
        return f"Redo triggered: {message}"
    raise RuntimeError(message or "redo_response failed")


# ── 7. Text Chat (existing) ───────────────────────────────────────────────────

@mcp.tool()
async def send_chat(prompt: str) -> str:
    """Send a text prompt to Gemini and return its text reply.

    This is a full round-trip: types the prompt, submits it, waits for
    Gemini to finish, and returns the text content of the response.
    Use submit_response + download_images instead when you need images.

    Requires engine_service.py to be running on port 18800 with an active
    logged-in Gemini session.

    Args:
        prompt: The text message to send to Gemini.

    Returns:
        Gemini's text reply.
    """
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{ENGINE_URL}/browser/chat",
            json={"text": prompt},
            timeout=240.0,
        )
        resp.raise_for_status()
        data = resp.json()

    if data.get("status") == "success":
        return data.get("text", "")
    raise RuntimeError(data.get("message", "Gemini chat failed with unknown error"))


if __name__ == "__main__":
    mcp.run()
