import asyncio
import sys
import os
from typing import Optional

import httpx
from mcp.server.fastmcp import FastMCP

ENGINE_URL = os.environ.get("GEMI_ENGINE_URL", "http://127.0.0.1:18800")

mcp = FastMCP("gemi-mcp")


async def _post(path: str, payload: dict | list | None = None, params: dict | None = None) -> dict:
    """POST to engine_service and return parsed JSON."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{ENGINE_URL}{path}",
            json=payload,
            params=params,
            timeout=300.0,
        )
        resp.raise_for_status()
        return resp.json()


# ── 1. Model / Tool Selection ─────────────────────────────────────────────────

@mcp.tool()
async def apply_settings(
    model: Optional[str] = None,
    tool: str = "default",
    thinking_level: Optional[str] = None,
    service: Optional[str] = None,
) -> str:
    """Switch Gemini or DeepSeek to a specific model, tool, and/or thinking level before generating.

    Call this before attaching files or submitting a prompt when you need a
    particular model, tool, or thinking level.
    Run discover_capabilities() first to see the live list of valid names.

    The engine validates selections against a live scan of the UI
    and returns a clear error with available options if any selection is invalid or stale.

    Args:
        model:          Display name of the model to select (partial match ok).
        tool:           Display name of the tool to enable (partial match ok).
                        Defaults to "default" which means "leave tool unchanged".
        thinking_level: Display name of the thinking level to select (e.g. "Low", "Medium", "High", "Extended", partial match ok).
        service:        Optional service name to target ("gemini" or "deepseek").

    Returns:
        Confirmation string or error description.
    """
    data = await _post("/browser/apply_settings", {
        "model": model,
        "tool": tool,
        "thinking_level": thinking_level,
        "service": service,
    })
    if data.get("status") == "success":
        parts = []
        if service:
            parts.append(f"service={service}")
        if model:
            parts.append(f"model={model}")
        if tool and tool.lower() != "default":
            parts.append(f"tool={tool}")
        if thinking_level:
            parts.append(f"thinking_level={thinking_level}")
        return f"Settings applied: {', '.join(parts)}" if parts else "No changes requested."
    return f"Error: {data.get('message', 'apply_settings failed')}"


@mcp.tool()
async def attach_files(file_paths: list[str], service: Optional[str] = None) -> str:
    """Attach one or more local files to the current prompt input.

    Uses smart incremental sync: files already attached are kept, missing ones
    are added, and extras are removed — minimising redundant uploads.

    Args:
        file_paths: Absolute paths to the files to attach.
                    Pass an empty list [] to clear all attachments.
        service:    Optional service name to target ("gemini" or "deepseek").

    Returns:
        Summary of how many files were added / removed.
    """
    data = await _post(
        "/browser/attach_files",
        payload=file_paths,
        params={"service": service} if service else None
    )
    if data.get("status") == "success":
        return (
            f"Attachments synced: +{data.get('added', 0)} added, "
            f"-{data.get('removed', 0)} removed, "
            f"{data.get('total_now', 0)} total."
        )
    raise RuntimeError(data.get("message", "attach_files failed"))


# ── 3. Prompt Input ───────────────────────────────────────────────────────────

@mcp.tool()
async def set_prompt(text: str, service: Optional[str] = None) -> str:
    """Type a prompt into the input box without submitting it.

    Use this to stage a prompt before calling submit_response, or to
    pre-fill text before attaching files.

    Args:
        text:    The prompt text to place in the input field.
        service: Optional service name to target ("gemini" or "deepseek").

    Returns:
        Confirmation that the prompt was filled.
    """
    data = await _post("/browser/prompt", {"text": text, "service": service})
    return f"Prompt staged: {str(data)}"


# ── 4. Submit & Monitor ───────────────────────────────────────────────────────

@mcp.tool()
async def submit_response(prompt: Optional[str] = None, service: Optional[str] = None) -> str:
    """Submit the current prompt and wait for an image or text response.

    Monitors the DOM until the response generation finishes, then returns whether it
    was successful, refused, quota was exceeded, etc.

    Call attach_files and set_prompt first if needed, then call this tool.
    Alternatively pass `prompt` here to type and submit in one step.

    Args:
        prompt:  Optional prompt text to inject and submit in one shot.
                 If None, submits whatever is already in the input box.
        service: Optional service name to target ("gemini" or "deepseek").

    Returns:
        Result status and message from the response monitor.
    """
    payload = {"text": prompt} if prompt else {}
    if service:
        payload["service"] = service
    data = await _post("/browser/submit", payload if payload else None)
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
    service: Optional[str] = None,
) -> str:
    """Download generated images from the last response to disk.

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
        service:  Optional service name to target ("gemini" or "deepseek").

    Returns:
        Number of images saved and their file paths.
    """
    payload = {
        "save_dir": save_dir,
        "naming": {"prefix": prefix, "padding": padding, "start": start},
        "meta": {},
        "service": service,
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
async def redo_response(service: Optional[str] = None) -> str:
    """Trigger the Redo (regenerate) action on the last response.

    Clicks the refresh/redo button and monitors until it finishes
    regenerating. Use this after a refused or unsatisfactory result before
    calling download_images again.

    Args:
        service: Optional service name to target ("gemini" or "deepseek").

    Returns:
        Confirmation that the redo action was triggered.
    """
    data = await _post("/browser/redo", params={"service": service} if service else None)
    status = data.get("status", "unknown")
    message = data.get("message", "")
    if status == "success":
        return f"Redo triggered: {message}"
    raise RuntimeError(message or "redo_response failed")


# ── 7. Text Chat (existing) ───────────────────────────────────────────────────

@mcp.tool()
async def send_chat(prompt: str, new_conversation: bool = True, service: Optional[str] = None) -> str:
    """Send a text prompt to Gemini or DeepSeek and return its text reply.

    This is a full round-trip: types the prompt, submits it, waits for
    the service to finish, and returns the text content of the response.
    Use submit_response + download_images instead when you need images.

    Args:
        prompt:           The text message to send.
        new_conversation: If True (default), starts a fresh chat before
                          sending. Set False to continue an existing conversation.
        service:          Optional service name to target ("gemini" or "deepseek").

    Returns:
        The text reply.
    """
    payload = {"text": prompt, "new_conversation": new_conversation}
    if service:
        payload["service"] = service
        
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{ENGINE_URL}/browser/chat",
            json=payload,
            timeout=240.0,
        )
        resp.raise_for_status()
        data = resp.json()

    if data.get("status") == "success":
        return data.get("text", "")
    raise RuntimeError(data.get("message", "Chat failed with unknown error"))


@mcp.tool()
async def get_last_response(service: Optional[str] = None) -> str:
    """Read whatever the service has generated so far in the current chat.

    Use this after send_chat times out — the browser tab may still be generating.
    Returns the current response text and a 'done' flag.
    Poll every few seconds until done=True.

    Args:
        service: Optional service name to target ("gemini" or "deepseek").
    """
    params = {"service": service} if service else None
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{ENGINE_URL}/browser/last_response", params=params, timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
    done = data.get("done", False)
    text = data.get("text", "")
    return f"done={done}\n\n{text}"


# ── 8. Reset / New Chat ───────────────────────────────────────────────────────

@mcp.tool()
async def new_chat(service: Optional[str] = None) -> str:
    """Clear conversation history and start a new chat session.

    Use this to reset conversation state, switch contexts, or isolate a new
    task from prior interactions. This clicks the "New chat" button.

    IMPORTANT — verify before conversing: after calling new_chat(), always
    confirm which service is active by sending a self-identification prompt
    (e.g. send_chat("What is your name?")) and checking the reply before
    proceeding with real tasks. This guards against stale service state.

    Args:
        service: Optional service name to target ("gemini" or "deepseek").

    Returns:
        Confirmation message.
    """
    data = await _post("/browser/new_chat", params={"service": service} if service else None)
    if data.get("status") == "success":
        return f"New chat started: {data.get('message', '')}"
    raise RuntimeError(data.get("message", "new_chat failed"))


# ── 9. Discover Capabilities ──────────────────────────────────────────────────

@mcp.tool()
async def discover_capabilities(service: Optional[str] = None) -> str:
    """Scan the UI dynamically via Playwright to discover available models, tools, and options.

    Triggers a live DOM scan of the web page to retrieve currently supported models,
    thinking levels, main tools, and sub-tools, updating the engine's capability cache.

    In addition to the available option lists, the returned payload contains the currently active
    'current_model' and 'current_thinking_level' values currently selected in the UI. This is
    useful for verifying if an apply_settings() call succeeded.

    Args:
        service: Optional service name to target ("gemini" or "deepseek").

    Returns:
        JSON string containing the discovered capabilities.
    """
    data = await _post("/browser/discover", params={"service": service} if service else None)
    if data.get("status") == "success":
        import json
        return json.dumps(data.get("data", {}), indent=2)
    raise RuntimeError(data.get("message", "discover_capabilities failed"))


# ── 10. Service Switching ─────────────────────────────────────────────────────

@mcp.tool()
async def switch_service(service: str) -> str:
    """Switch the active AI service provider (e.g. 'gemini' or 'deepseek').

    This changes which web UI the engine drives. After switching, all subsequent
    tool calls (send_chat, new_chat, etc.) target the new service.

    Currently supported services:
      - "gemini"   — Google Gemini web UI (default)
      - "deepseek" — DeepSeek chat web UI

    IMPORTANT — verify after switching: always follow switch_service() with
    send_chat("What is your name?") to confirm the new service self-identifies
    correctly before sending real prompts.

    Args:
        service: Name of the service to switch to (case-insensitive).

    Returns:
        Confirmation message or error description.
    """
    data = await _post("/browser/switch_service", {"service": service})
    if data.get("status") == "success":
        return f"Switched to service: {service}. {data.get('message', '')}"
    return f"Error: {data.get('message', 'switch_service failed')}"


if __name__ == "__main__":
    mcp.run()

