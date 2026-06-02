import json
import asyncio
import logging
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from .services.kml_parser import parse_polygon_coordinates
from .services.simulation_engine import SimulationEngine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Simulador de Incendios Forestales")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4200"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/simulation/initial")
async def get_initial_data():
    try:
        coordinates = parse_polygon_coordinates()
    except Exception as e:
        logger.error("Error parsing KML: %s", e)
        return {"zoneCoordinates": [], "wind": {"speed": 0, "direction": 0}}

    return {
        "zoneCoordinates": coordinates,
        "wind": {"speed": 15.5, "direction": 180},
    }


@app.websocket("/api/simulation/ws")
async def simulation_websocket(ws: WebSocket):
    await ws.accept()
    logger.info("WebSocket conectado")

    engine = SimulationEngine()
    send_queue: asyncio.Queue = asyncio.Queue()

    async def send_from_queue():
        while True:
            payload = await send_queue.get()
            try:
                await ws.send_text(json.dumps(payload))
            except Exception:
                break

    queue_worker = asyncio.create_task(send_from_queue())

    def send_callback(updates: list[dict]) -> None:
        send_queue.put_nowait(updates)

    engine_task: asyncio.Task | None = None

    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            action = msg.get("action")

            if action == "start":
                config = msg.get("config", {})
                wind_speed = config.get("windSpeed", 15.5)
                wind_direction = config.get("windDirection", 180)
                ignition = config.get("ignitionPoint", [0, 0])
                ignition_lat, ignition_lng = ignition

                coords = parse_polygon_coordinates()

                engine.configure(
                    wind_speed=wind_speed,
                    wind_direction=wind_direction,
                    ignition_lat=ignition_lat,
                    ignition_lng=ignition_lng,
                    zone_coords=coords,
                    send_callback=send_callback,
                )
                engine.start()

                if engine_task and not engine_task.done():
                    engine_task.cancel()
                engine_task = asyncio.create_task(engine.run_loop())

                logger.info(
                    "Simulación iniciada: viento=%s km/h dir=%s° ignición=%s,%s",
                    wind_speed,
                    wind_direction,
                    ignition_lat,
                    ignition_lng,
                )

            elif action == "pause":
                engine.pause()
                logger.info("Simulación pausada")

            elif action == "stop":
                engine.stop()
                if engine_task and not engine_task.done():
                    engine_task.cancel()
                    engine_task = None
                logger.info("Simulación detenida")

    except WebSocketDisconnect:
        logger.info("WebSocket desconectado")
    except Exception as e:
        logger.error("Error en WebSocket: %s", e)
    finally:
        engine.stop()
        if engine_task and not engine_task.done():
            engine_task.cancel()
        queue_worker.cancel()
        try:
            await ws.close()
        except Exception:
            pass
