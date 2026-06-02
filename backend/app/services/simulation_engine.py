import math
import asyncio
import os
import json
import logging
from enum import Enum
from typing import Callable

import requests
import rasterio

logger = logging.getLogger(__name__)

OPENTOPOGRAPHY_API_KEY = "5090635d399f89371c7647df4ff02716"

DEM_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
DEM_PATH = os.path.join(DEM_DIR, "elevacion.tif")
BOUNDS_PATH = os.path.join(DEM_DIR, "elevacion_bounds.json")

DEFAULT_HUMIDITY = 0.4
DEFAULT_ELEVATION = 1000.0


class CellStatus(str, Enum):
    COMBUSTIBLE = "combustible"
    FUEGO = "fuego"
    QUEMADO = "quemado"

    def __str__(self) -> str:
        return self.value


SendCallback = Callable[[list[dict]], None]


class SimulationEngine:
    GRID_SIZE = 100

    def __init__(self) -> None:
        self.grid: list[list[CellStatus]] = []
        self.rows: int = 0
        self.cols: int = 0
        self.running = False
        self.paused = False
        self._send_callback: SendCallback | None = None
        self._task: asyncio.Task | None = None
        self._zone_bounds: tuple[float, float, float, float] = (0, 0, 0, 0)
        self.elevation_grid: list[list[float]] | None = None
        self.wind_speed: float = 0
        self.wind_direction: float = 0
        self._humidity: float = DEFAULT_HUMIDITY

    def configure(
        self,
        wind_speed: float,
        wind_direction: float,
        ignition_lat: float,
        ignition_lng: float,
        zone_coords: list[list[float]],
        send_callback: SendCallback,
        humidity: float = DEFAULT_HUMIDITY,
    ) -> None:
        self._compute_zone_bounds(zone_coords)
        self._download_elevation_dem()
        self._load_real_elevation_dem()
        self._build_grid()
        self.wind_speed = wind_speed
        self.wind_direction = wind_direction
        self._humidity = humidity
        self._send_callback = send_callback

        row, col = self._geo_to_grid(ignition_lat, ignition_lng)
        self.grid[row][col] = CellStatus.FUEGO

    # ------------------------------------------------------------------
    # Elevation DEM download & loading
    # ------------------------------------------------------------------

    def _download_elevation_dem(self) -> None:
        if os.path.exists(DEM_PATH) and os.path.exists(BOUNDS_PATH):
            try:
                with open(BOUNDS_PATH, "r") as f:
                    saved = json.load(f)
                saved_bounds = (
                    saved["lat_min"],
                    saved["lat_max"],
                    saved["lng_min"],
                    saved["lng_max"],
                )
                if all(
                    abs(a - b) < 1e-5
                    for a, b in zip(saved_bounds, self._zone_bounds)
                ):
                    logger.info("DEM ya descargado y bounds coinciden, omitiendo descarga")
                    return
                else:
                    logger.info("Bounds cambiaron, redescargando DEM...")
            except Exception:
                logger.warning("Error leyendo bounds JSON, redescargando...")
        else:
            logger.info("DEM no encontrado, descargando...")

        os.makedirs(DEM_DIR, exist_ok=True)

        lat_min, lat_max, lng_min, lng_max = self._zone_bounds
        params = {
            "demtype": "SRTMGL1",
            "south": lat_min,
            "north": lat_max,
            "west": lng_min,
            "east": lng_max,
            "outputFormat": "GTiff",
            "apikey": OPENTOPOGRAPHY_API_KEY,
        }

        try:
            resp = requests.get(
                "https://portal.opentopography.org/API/globaldem",
                params=params,
                stream=True,
                timeout=60,
            )
            if resp.status_code != 200:
                logger.error(
                    "Error al descargar DEM: HTTP %s - %s",
                    resp.status_code,
                    resp.text[:200],
                )
                return

            with open(DEM_PATH, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)

            with open(BOUNDS_PATH, "w") as f:
                json.dump(
                    {
                        "lat_min": lat_min,
                        "lat_max": lat_max,
                        "lng_min": lng_min,
                        "lng_max": lng_max,
                    },
                    f,
                )

            logger.info("DEM descargado exitosamente en %s", DEM_PATH)
        except requests.RequestException as e:
            logger.error("Error de conexión al descargar DEM: %s", e)

    def _load_real_elevation_dem(self) -> None:
        self.elevation_grid = [
            [0.0 for _ in range(self.cols)] for _ in range(self.rows)
        ]

        if not os.path.exists(DEM_PATH):
            logger.info("Archivo DEM no encontrado, usando elevación plana")
            return

        try:
            with rasterio.open(DEM_PATH) as dataset:
                band = dataset.read(1)
                nodata = dataset.nodata

                for r in range(self.rows):
                    for c in range(self.cols):
                        lat, lng = self._grid_to_geo(r, c)
                        try:
                            raster_row, raster_col = dataset.index(lng, lat)
                            if 0 <= raster_row < band.shape[0] and 0 <= raster_col < band.shape[1]:
                                value = float(band[raster_row, raster_col])
                                if nodata is None or value != nodata:
                                    self.elevation_grid[r][c] = value
                        except Exception:
                            pass

            logger.info("DEM cargado en elevation_grid (%dx%d)", self.rows, self.cols)
        except Exception as e:
            logger.error("Error al leer el DEM: %s", e)

    # ------------------------------------------------------------------
    # Zone bounds & grid
    # ------------------------------------------------------------------

    def _compute_zone_bounds(self, coords: list[list[float]]) -> None:
        lats = [p[0] for p in coords]
        lngs = [p[1] for p in coords]
        self._zone_bounds = (
            min(lats),
            max(lats),
            min(lngs),
            max(lngs),
        )
        lat_span = self._zone_bounds[1] - self._zone_bounds[0]
        lng_span = self._zone_bounds[3] - self._zone_bounds[2]
        if lat_span == 0:
            self._zone_bounds = (
                self._zone_bounds[0] - 0.001,
                self._zone_bounds[1] + 0.001,
                self._zone_bounds[2],
                self._zone_bounds[3],
            )
        if lng_span == 0:
            self._zone_bounds = (
                self._zone_bounds[0],
                self._zone_bounds[1],
                self._zone_bounds[2] - 0.001,
                self._zone_bounds[3] + 0.001,
            )
        self.rows = self.GRID_SIZE
        self.cols = self.GRID_SIZE

    def _build_grid(self) -> None:
        self.grid = [
            [CellStatus.COMBUSTIBLE for _ in range(self.cols)]
            for _ in range(self.rows)
        ]

    def _geo_to_grid(self, lat: float, lng: float) -> tuple[int, int]:
        lat_min, lat_max, lng_min, lng_max = self._zone_bounds
        lat_span = lat_max - lat_min
        lng_span = lng_max - lng_min
        row = int(((lat_max - lat) / lat_span) * self.rows)
        col = int(((lng - lng_min) / lng_span) * self.cols)
        row = max(0, min(row, self.rows - 1))
        col = max(0, min(col, self.cols - 1))
        return row, col

    def _grid_to_geo(self, row: int, col: int) -> tuple[float, float]:
        lat_min, lat_max, lng_min, lng_max = self._zone_bounds
        lat_step = (lat_max - lat_min) / self.rows
        lng_step = (lng_max - lng_min) / self.cols
        lat = lat_max - (row + 0.5) * lat_step
        lng = lng_min + (col + 0.5) * lng_step
        return lat, lng

    # ------------------------------------------------------------------
    # INP physical model helpers
    # ------------------------------------------------------------------

    def _get_elevation(self, r: int, c: int) -> float:
        if self.elevation_grid is not None and self.elevation_grid:
            try:
                return self.elevation_grid[r][c]
            except IndexError:
                pass
        return DEFAULT_ELEVATION

    def _get_slope_factor(self, r1: int, c1: int, r2: int, c2: int) -> float:
        e1 = self._get_elevation(r1, c1)
        e2 = self._get_elevation(r2, c2)
        elev_diff = e2 - e1
        lat_min, lat_max, lng_min, lng_max = self._zone_bounds
        lat_per_cell = (lat_max - lat_min) / self.rows
        lng_per_cell = (lng_max - lng_min) / self.cols
        dr = (r2 - r1) * lat_per_cell
        dc = (c2 - c1) * lng_per_cell
        distance = math.hypot(dr, dc)
        if distance < 1e-10:
            return 1.0
        slope_ratio = elev_diff / distance
        P = 1.0 + slope_ratio * 3.0
        return max(0.3, min(P, 3.0))

    def _get_vegetation_params(self, r: int, c: int) -> tuple[float, float, float]:
        elev = self._get_elevation(r, c)
        if elev >= 2000:
            return (1.2, 0.3, 0.05)
        elif elev >= 1500:
            return (1.1, 0.5, 0.15)
        elif elev >= 1000:
            return (1.0, 0.7, 0.30)
        elif elev >= 500:
            return (0.85, 0.9, 0.45)
        else:
            return (0.7, 1.1, 0.60)

    def _spread_probability_inp(
        self,
        r: int,
        c: int,
        nr: int,
        nc: int,
        neighbor_angle: float,
    ) -> float:
        if self.wind_speed <= 0:
            return 0.3
        angle_diff = (neighbor_angle - self.wind_direction + 360) % 360
        if angle_diff > 180:
            angle_diff = 360 - angle_diff
        alignment = math.cos(math.radians(angle_diff))
        alignment = max(0.0, alignment)
        V_eff = self.wind_speed * alignment
        K, C, h_extra = self._get_vegetation_params(nr, nc)
        P = self._get_slope_factor(r, c, nr, nc)
        H = self._humidity * 0.35 + h_extra
        if H < 0.01:
            H = 0.01
        INP = (K * C * P * (V_eff ** 2)) / H
        prob = INP / (INP + 1.0)
        return min(prob, 0.95)

    # ------------------------------------------------------------------
    # Control
    # ------------------------------------------------------------------

    def start(self) -> None:
        self.running = True
        self.paused = False

    def pause(self) -> None:
        self.paused = True

    def stop(self) -> None:
        self.running = False
        self.paused = False

    async def run_loop(self) -> None:
        step = 0
        while self.running:
            if self.paused:
                await asyncio.sleep(0.5)
                continue

            await asyncio.sleep(0.8)

            updates = self._step()
            if self._send_callback:
                self._send_callback(updates)

            if not self._has_burning():
                self.running = False
                break

            step += 1

    # ------------------------------------------------------------------
    # Simulation step & propagation (INP-based)
    # ------------------------------------------------------------------

    def _step(self) -> list[dict]:
        new_grid = [row[:] for row in self.grid]
        updates: list[dict] = []

        for r in range(self.rows):
            for c in range(self.cols):
                if self.grid[r][c] == CellStatus.FUEGO:
                    new_grid[r][c] = CellStatus.QUEMADO
                    updates.append({"row": r, "col": c, "status": CellStatus.QUEMADO})
                    self._spread_to_neighbors(r, c, new_grid, updates)

        self.grid = new_grid
        return updates

    def _spread_to_neighbors(
        self,
        r: int,
        c: int,
        new_grid: list[list[CellStatus]],
        updates: list[dict],
    ) -> None:
        directions = [
            (-1, -1, 225),
            (-1, 0, 180),
            (-1, 1, 135),
            (0, -1, 270),
            (0, 1, 90),
            (1, -1, 315),
            (1, 0, 0),
            (1, 1, 45),
        ]

        for dr, dc, angle in directions:
            nr, nc = r + dr, c + dc
            if not (0 <= nr < self.rows and 0 <= nc < self.cols):
                continue
            if new_grid[nr][nc] != CellStatus.COMBUSTIBLE:
                continue

            prob = self._spread_probability_inp(r, c, nr, nc, angle)
            if prob > 0 and (hash((nr, nc, id(self))) % 1000) / 1000 < prob:
                new_grid[nr][nc] = CellStatus.FUEGO
                updates.append({"row": nr, "col": nc, "status": CellStatus.FUEGO})

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _has_burning(self) -> bool:
        return any(
            cell == CellStatus.FUEGO for row in self.grid for cell in row
        )

    @property
    def active(self) -> bool:
        return self.running and not self.paused
