from __future__ import annotations

import asyncio
import json
import re
import socket
import uuid
from pathlib import Path
from typing import Any

from deepgram import AsyncDeepgramClient, DeepgramClient
from deepgram.core.events import EventType
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
import httpx
from config_store import load_settings, save_settings


ROOT = Path(__file__).parent
app = FastAPI(title="OBS Live Deepgram Multi Transcribe")
overlay_clients: set[WebSocket] = set()
overlay_history: list[dict[str, Any]] = []
translation_history: dict[str, dict[str, str]] = {}
translation_mode_enabled = bool(load_settings().get("translationMode", False))
LIBRETRANSLATE_URL = "https://translate.argosopentech.com"
DEFAULT_OVERLAY_COLORS = {
    "1": "#ff5964", "2": "#ff9f43", "3": "#d789ff",
    "4": "#ffd166", "5": "#ff7892", "6": "#e6a3ff",
}
_saved_overlay_colors = load_settings().get("overlayColors", {})
overlay_colors = {
    speaker: color if isinstance(color, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", color) else DEFAULT_OVERLAY_COLORS[speaker]
    for speaker, color in {
        **DEFAULT_OVERLAY_COLORS,
        **(_saved_overlay_colors if isinstance(_saved_overlay_colors, dict) else {}),
    }.items()
    if speaker in DEFAULT_OVERLAY_COLORS
}


@app.get("/api/config")
async def get_config() -> dict[str, Any]:
    return {
        "port": int(getattr(app.state, "active_port", 8765)),
        "apiKey": str(load_settings().get("apiKey", "")),
    }


@app.get("/api/overlay/colors")
async def get_overlay_colors() -> dict[str, Any]:
    return {"colors": overlay_colors}


@app.post("/api/overlay/colors")
async def set_overlay_colors(payload: dict[str, Any]) -> dict[str, Any]:
    colors = payload.get("colors", {})
    if not isinstance(colors, dict):
        return {"ok": False, "message": "Format de couleurs invalide."}
    for speaker, color in colors.items():
        if speaker in DEFAULT_OVERLAY_COLORS and isinstance(color, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            overlay_colors[speaker] = color.lower()
    save_settings(overlayColors=overlay_colors)
    await broadcast({"type": "settings", "colors": overlay_colors})
    return {"ok": True, "colors": overlay_colors}


@app.post("/api/config/key")
async def save_api_key(payload: dict[str, Any]) -> dict[str, bool]:
    api_key = str(payload.get("apiKey", "")).strip()
    save_settings(apiKey=api_key)
    return {"ok": True}


@app.get("/api/translation/config")
async def get_translation_config() -> dict[str, Any]:
    settings = load_settings()
    return {
        "targetLanguage": str(settings.get("translationTarget", "en")).lower(),
        "enabled": translation_mode_enabled,
    }


@app.post("/api/translation/config")
async def set_translation_config(payload: dict[str, Any]) -> dict[str, Any]:
    target = str(payload.get("targetLanguage", "en")).lower()
    if not re.fullmatch(r"[a-z]{2,3}(?:-[a-z0-9]{2,8})?", target):
        return {"ok": False, "message": "Code de langue cible invalide."}
    save_settings(translationTarget=target)
    await broadcast({"type": "settings", "translationTarget": target})
    return {"ok": True, "targetLanguage": target}


@app.post("/api/translation/mode")
async def set_translation_mode(payload: dict[str, Any]) -> dict[str, Any]:
    global translation_mode_enabled
    enabled = bool(payload.get("enabled", False))
    translation_mode_enabled = enabled
    save_settings(translationMode=enabled)
    await broadcast({"type": "translation_mode", "enabled": enabled})
    return {"ok": True, "enabled": enabled}


async def libretranslate(http: httpx.AsyncClient, text: str, source: str, target: str) -> str:
    response = await http.post(
        f"{LIBRETRANSLATE_URL}/translate",
        json={"q": text, "source": source or "auto", "target": target.lower(), "format": "text"},
    )
    response.raise_for_status()
    translated = response.json().get("translatedText", "")
    if not translated:
        raise RuntimeError("LibreTranslate n’a renvoyé aucune traduction.")
    return str(translated)


@app.get("/api/translation/languages")
async def translation_languages() -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(f"{LIBRETRANSLATE_URL}/languages")
            response.raise_for_status()
            return {"languages": response.json()}
    except Exception as exc:
        return {"languages": [], "message": str(exc)}


@app.post("/api/config/port")
async def set_port(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        port = int(payload.get("port", 0))
    except (TypeError, ValueError):
        return {"ok": False, "message": "Le port doit être un nombre."}
    if not 1024 <= port <= 65535:
        return {"ok": False, "message": "Choisissez un port entre 1024 et 65535."}
    active_port = int(getattr(app.state, "active_port", 8765))
    if port == active_port:
        return {"ok": True, "port": port, "restarting": False}
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", port))
    except OSError:
        return {"ok": False, "message": f"Le port {port} est déjà utilisé."}
    app.state.requested_port = port
    app.state.active_server.should_exit = True
    return {"ok": True, "port": port, "restarting": True}


@app.post("/api/billing/snapshot")
async def billing_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    api_key = str(payload.get("apiKey", "")).strip()
    if not api_key:
        return {"ok": False, "message": "Ajoutez votre clé API Deepgram dans la configuration."}

    def read_balances() -> list[dict[str, Any]]:
        client = DeepgramClient(api_key=api_key)
        projects_response = client.manage.v1.projects.list()
        projects = projects_response.projects or []
        snapshots: list[dict[str, Any]] = []
        for project in projects:
            response = client.manage.v1.projects.billing.balances.list(project_id=project.project_id)
            balances = response.balances or []
            usd = sum(float(item.amount or 0) for item in balances if (item.units or "").upper() == "USD")
            snapshots.append({
                "projectId": project.project_id,
                "name": project.name or project.project_id,
                "balanceUsd": round(usd, 4),
            })
        return snapshots

    try:
        projects = await asyncio.to_thread(read_balances)
    except Exception as exc:
        return {
            "ok": False,
            "message": "Solde Deepgram indisponible. Vérifiez les permissions de gestion de votre clé ; vous pouvez aussi saisir le crédit restant manuellement.",
            "detail": str(exc),
        }
    if not projects:
        return {"ok": False, "message": "Aucun projet Deepgram accessible avec cette clé."}
    return {"ok": True, "projects": projects}


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(ROOT / "ui" / "index.html")


@app.get("/overlay")
async def overlay() -> FileResponse:
    return FileResponse(ROOT / "ui" / "overlay.html")


@app.get("/assets/watermark-red.svg")
async def watermark() -> FileResponse:
    return FileResponse(ROOT / "ui" / "assets" / "watermark-red.svg", media_type="image/svg+xml")


@app.get("/assets/dragonship-tools.png")
async def creator_logo() -> FileResponse:
    return FileResponse(ROOT / "ui" / "assets" / "dragonship-tools.png", media_type="image/png")


async def broadcast(payload: dict[str, Any]) -> None:
    if payload.get("type") == "transcript":
        data = payload.get("data") or {}
        alternatives: list[dict[str, Any]] = []
        channel = data.get("channel") if isinstance(data, dict) else None
        if isinstance(channel, dict):
            alternatives = channel.get("alternatives") or []
        if not alternatives and isinstance(data, dict):
            results = data.get("results")
            channels = results.get("channels") if isinstance(results, dict) else None
            if isinstance(channels, list) and channels and isinstance(channels[0], dict):
                alternatives = channels[0].get("alternatives") or []
        if alternatives and alternatives[0].get("transcript"):
            overlay_history.append(payload)
            del overlay_history[:-12]
            recent_ids = {
                item.get("data", {}).get("_translationId")
                for item in overlay_history
                if isinstance(item.get("data"), dict)
            }
            for translation_id in tuple(translation_history):
                if translation_id not in recent_ids:
                    translation_history.pop(translation_id, None)
    elif payload.get("type") == "translation":
        translation_id = payload.get("id")
        if translation_id:
            translation_history[str(translation_id)] = {"text": str(payload.get("text", ""))}
            while len(translation_history) > 12:
                translation_history.pop(next(iter(translation_history)))
    dead: list[WebSocket] = []
    for client in tuple(overlay_clients):
        try:
            await client.send_json(payload)
        except Exception:
            dead.append(client)
    for client in dead:
        overlay_clients.discard(client)


@app.websocket("/ws/overlay")
async def overlay_socket(ws: WebSocket) -> None:
    await ws.accept()
    overlay_clients.add(ws)
    try:
        await ws.send_json({
            "type": "settings", "colors": overlay_colors,
            "translationTarget": load_settings().get("translationTarget", "en"),
        })
        await ws.send_json({"type": "translation_mode", "enabled": translation_mode_enabled})
        # Replay a small recent buffer so OBS sources opened after transcription
        # has started do not appear empty until the next phrase is spoken.
        for payload in overlay_history:
            await ws.send_json(payload)
        for translation_id, translation in translation_history.items():
            await ws.send_json({"type": "translation", "id": translation_id, **translation})
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        overlay_clients.discard(ws)


@app.websocket("/ws/live")
async def live_socket(ws: WebSocket) -> None:
    await ws.accept()
    client: AsyncDeepgramClient | None = None
    try:
        setup = json.loads(await ws.receive_text())
        api_key = str(setup.get("apiKey", "")).strip()
        if not api_key:
            await ws.send_json({"type": "error", "message": "Ajoutez votre clé API Deepgram."})
            return

        client = AsyncDeepgramClient(api_key=api_key)
        loop = asyncio.get_running_loop()
        event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        translation_queue: asyncio.Queue[dict[str, str]] = asyncio.Queue(maxsize=100)
        saved_translation = load_settings()
        translation_target = str(saved_translation.get("translationTarget", "en")).lower()
        connection_opened = asyncio.Event()
        listener_task: asyncio.Task | None = None
        translation_task: asyncio.Task | None = None

        async with client.listen.v1.connect(
            model=setup.get("model", "nova-3"),
            language=setup.get("language", "fr"),
            encoding="linear16",
            sample_rate=16000,
            channels=1,
            diarize=True,
            punctuate=True,
            interim_results=True,
            utterance_end_ms="1000",
            vad_events=True,
        ) as connection:
            def on_message(message: Any) -> None:
                if hasattr(message, "model_dump"):
                    result = message.model_dump(mode="json")
                elif isinstance(message, dict):
                    result = message
                else:
                    result = json.loads(str(message))
                loop.call_soon_threadsafe(event_queue.put_nowait, result)

            def on_open(_: Any = None) -> None:
                loop.call_soon_threadsafe(connection_opened.set)

            connection.on(EventType.OPEN, on_open)
            connection.on(EventType.MESSAGE, on_message)
            connection.on(EventType.ERROR, lambda error: loop.call_soon_threadsafe(
                event_queue.put_nowait, {"type": "error", "message": str(error)}
            ))
            # start_listening owns the receive loop; it must run concurrently
            # with forwarding microphone audio to Deepgram.
            listener_task = asyncio.create_task(connection.start_listening())
            try:
                await asyncio.wait_for(connection_opened.wait(), timeout=15)
            except asyncio.TimeoutError as exc:
                raise RuntimeError("Deepgram n’a pas ouvert la connexion dans les 15 secondes.") from exc
            await ws.send_json({"type": "status", "state": "connected"})

            async def translate_results() -> None:
                async with httpx.AsyncClient(timeout=12.0) as translator:
                    while True:
                        item = await translation_queue.get()
                        try:
                            if item["source"] != "auto" and item["source"] == item["target"]:
                                translated_text = item["text"]
                            else:
                                translated_text = await libretranslate(
                                    translator, item["text"], item["source"], item["target"]
                                )
                            message = {"type": "translation", "id": item["id"], "text": translated_text, "target": item["target"]}
                            await ws.send_json(message)
                            await broadcast(message)
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            try:
                                await ws.send_json({"type": "translation_error", "message": f"LibreTranslate : {exc}"})
                            except Exception:
                                pass

            translation_task = asyncio.create_task(translate_results())

            async def forward_results() -> None:
                while True:
                    result = await event_queue.get()
                    event_type = str(result.get("type", "")).lower()
                    if event_type == "error":
                        details = " · ".join(
                            str(result.get(field, "")).strip()
                            for field in ("description", "code", "message")
                            if result.get(field)
                        ) or "Erreur Deepgram sans détails."
                        await ws.send_json({"type": "error", "message": details})
                        continue
                    channels = result.get("results", {}).get("channels", []) if isinstance(result.get("results"), dict) else []
                    alternatives = result.get("channel", {}).get("alternatives", []) if isinstance(result.get("channel"), dict) else []
                    if not alternatives and channels and isinstance(channels[0], dict):
                        alternatives = channels[0].get("alternatives", [])
                    alt = alternatives[0] if alternatives and isinstance(alternatives[0], dict) else {}
                    if result.get("is_final") and alt.get("transcript"):
                        translation_id = uuid.uuid4().hex
                        result["_translationId"] = translation_id
                        if translation_mode_enabled:
                            source_languages = alt.get("languages") or []
                            source_language = str(source_languages[0]) if source_languages else ""
                            if not source_language and isinstance(alt.get("words"), list):
                                source_language = next((str(word.get("language")) for word in alt["words"] if isinstance(word, dict) and word.get("language")), "")
                            if not source_language:
                                source_language = str(setup.get("language", "auto"))
                            source_language = source_language.split("-")[0].lower()
                            if source_language == "multi":
                                source_language = "auto"
                            try:
                                translation_queue.put_nowait({
                                    "id": translation_id,
                                    "text": str(alt["transcript"]),
                                    "source": source_language,
                                    "target": translation_target,
                                })
                            except asyncio.QueueFull:
                                await ws.send_json({"type": "translation_error", "message": "File de traduction pleine ; des segments peuvent manquer."})
                    await ws.send_json({"type": "deepgram_event", "event": result.get("type", "Message")})
                    await ws.send_json({"type": "transcript", "data": result})
                    try:
                        await broadcast({"type": "transcript", "data": result})
                    except Exception as exc:
                        # An overlay/history issue must never stop forwarding
                        # subsequent Deepgram results to the dashboard.
                        await ws.send_json({"type": "error", "message": f"Erreur de diffusion overlay : {exc}"})

            result_task = asyncio.create_task(forward_results())
            received_audio = False
            audio_bytes = 0
            try:
                while True:
                    try:
                        packet = await asyncio.wait_for(ws.receive(), timeout=5)
                    except asyncio.TimeoutError:
                        # Keep the upstream socket alive while the user is still
                        # choosing/checking an input. Deepgram closes otherwise
                        # with NET0001 before the UI can report a missing signal.
                        await connection.send_keep_alive()
                        continue
                    if packet.get("type") == "websocket.disconnect":
                        break
                    raw = packet.get("bytes")
                    if raw:
                        await connection.send_media(raw)
                        audio_bytes += len(raw)
                        if not received_audio:
                            received_audio = True
                        # A periodic byte count makes it possible to distinguish
                        # capture failures from upstream transcription failures.
                        if audio_bytes == len(raw) or audio_bytes % 32000 < len(raw):
                            await ws.send_json({"type": "audio_received", "bytes": audio_bytes})
                    elif packet.get("text"):
                        if packet["text"] == "STOP":
                            break
            finally:
                result_task.cancel()
                try:
                    await result_task
                except asyncio.CancelledError:
                    pass
                if translation_task:
                    translation_task.cancel()
                    try:
                        await translation_task
                    except asyncio.CancelledError:
                        pass
                try:
                    await connection.send_finalize()
                    await connection.send_close_stream()
                except Exception:
                    pass
                if listener_task and not listener_task.done():
                    listener_task.cancel()
                    try:
                        await listener_task
                    except asyncio.CancelledError:
                        pass
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        try:
            await ws.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass
    finally:
        if client is not None:
            await client.close()
